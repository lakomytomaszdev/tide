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
| `processes` | string | stan migawki procesów, z której policzono `pid`, `canActivate` i żywotność (K-98 #4, PT-345): `none` — przed pierwszym wynikiem sondy (brak danych: sesja nie jest przez to `ended`), `fresh`, `stale` — ostatni wynik starszy niż trzy ticki (zawieszona sonda; zostaje ostatnia pełna migawka), `error` — ostatnia sonda rzuciła wyjątek. Bez pola przy `--no-processes` |
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

**Drugi stopień: model ocenia wątpliwe końcówki** (PT-42, decyzja 93). Reguły przegapiają prośby bez „?” („**Krok 1:** uruchom ten select i wklej mi wynik”, „Daj znać.”), więc `question.doubtful(text)` wskazuje końcówki, w których reguły nie widzą pytania (`ending_question` puste), a w ostatnich trzech zdaniach (i punktach listy pod nimi) jest zdanie z „?”, zdanie zaczynające się czasownikiem w trybie rozkazującym z listy (`uruchom`, `wklej`, `sprawdź`, `podaj`, `potwierdź`… także po „Krok N:”, „Teraz”, „Proszę”) albo „daj znać”/`wklej`/`prześlij`/`odpisz`. „Dam znać”, „Jeśli chcesz, mogę też…”, „Gotowe.” i każda wiadomość z regułą „tak” nie są wątpliwe i modelu nie kosztują. Wątpliwą końcówkę ocenia `agents/classify.py`: `reply_question` pyta `Classifier.judge(klucz, tekst)`, który **nie czeka na model** — zwraca zapamiętany werdykt albo `None` (wtedy liczą same reguły) i planuje ocenę w osobnym wątku `tide-classify`; po wyniku `AgentMonitor.classified` przelicza stan w wątku Qt. Stan z werdyktu „tak” ma ten sam kształt co z reguł: `waiting`, `reason: reply`, `sinceTs` — chwila wiadomości, `waitingText` — dwa ostatnie zdania końcówki (najwyżej 240 znaków). Wywołanie: `models.chat(role="classify")` przez kolejkę modeli (priorytet jak opisy kart, przed raportem; jedno wywołanie naraz na serwer, ocena ustępuje czekającemu raportowi najwyżej 30 s), ostatnie 1200 znaków wiadomości, schemat `{"waits": bool}`, `think: false`, `temperature: 0`, limit 20 s od startu wywołania, 120 s czekania w kolejce. Werdykt pamiętany na uuid wpisu (Claude Code; Codex — sesja i skrót tekstu): w pamięci i w `$XDG_STATE_HOME/tide/classify.json` (`{"version": 1, "results": {klucz: {"waits": bool, "at": ts}}}`, 500 najnowszych, zapis atomowy), więc ta sama wiadomość jest oceniana najwyżej raz także po restarcie usługi. Błąd (brak serwera, zły JSON, zły kształt, limit czasu) → werdykt się nie zapisuje, liczą same reguły, przyczyna jest w dzienniku usługi, a ta sama wiadomość wraca do oceny najwcześniej po 5 min. Rola `classify` bez modelu, z punktem nieznanym albo pominiętym lub niedostępna (np. model chmurowy bez jawnego punktu) wyłącza ocenę: ostrzeżenie w dzienniku (raz na powód), ponowne sprawdzenie ustawień po minucie. Model chmurowy działa w tej roli tylko przy jawnym punkcie w `models.json` (`"classify": "punkt:gpt-oss:120b-cloud"`): wtedy ocena idzie tam i tylko tam, a przy jego błędzie liczą same reguły — bez zastępstwa innym punktem. Przy roli lokalnej punkt w chmurze nie jest wołany nigdy (decyzja 70). Z `--no-models` ocena nie działa (`--print-once` i `scan()` też liczą same reguły).

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

**Skrót Meta+A.** Skrypt KWin `tide-agent-jump` (`kwin/agent-jump`, instalacja `make install-agent-jump`) rejestruje skrót przez `registerShortcut` i woła `ActivateLongestWaiting` na `/Agents`. Usługa odświeża stan i procesy (sonda procesów rozpoczęta po wciśnięciu, poza wątkiem głównym — niżej, „Sonda procesów poza wątkiem głównym”), wybiera czekającą sesję z najwcześniejszym `sinceTs` spośród tych, do których okna da się przejść (czekająca w tmux czy przez ssh jest pomijana), i aktywuje okno tak jak `ActivateSession` (Konsole: najpierw karta). Gdy nic nie czeka — zwraca `false` bez żadnego komunikatu; sesje `done` nie są zapasowym celem (wyżej, „Meta+A tylko do czekających”). Jeśli usługa nie działa, D-Bus uruchomi ją przy pierwszym naciśnięciu.

**Sonda procesów poza wątkiem głównym (K-98 #4, PT-345).** `processes.probe` (całe `/proc`, u agentów także `fd`)
biegnie w jednym stałym wątku `tide-process-probe` (`procprobe.ProcessProber`), a nie w wątku głównym, który obsługuje
D-Bus. Tick monitora sesji (co 5 s), zmiana dziennika i wynik oceny tury przeliczają listę na ostatniej opublikowanej
migawce procesów i proszą o nową sondę bez czekania; jej wynik wraca sygnałem do wątku głównego, podmienia migawkę
jednym przypisaniem (stara albo nowa tabela, nigdy częściowa) i przelicza listę ponownie. Prośby w trakcie sondy
zlewają się w jedną następną; wyjątek sondy daje stan `error` i nie zatrzymuje następnej, a zawieszona sonda trzyma
tylko swój wątek (odczyty dostają ostatnią migawkę ze stanem `stale`). Akcje użytkownika działają wyłącznie na sondzie
rozpoczętej po wywołaniu, nie na opublikowanej migawce ani na sondzie trwającej w chwili wywołania: `ActivateSession`
i `ActivateLongestWaiting` odpowiadają z opóźnieniem (delayed reply), gdy przyjdzie taka sonda, a po 2 s bez niej —
`false` bez skutku. Wywołania z wnętrza usługi (przycisk „Przejdź” powiadomienia, `/Coordination Activate`, KRunner,
otwarcie wątku Codex Desktop) czekają na nią jawnie najwyżej `PROBE_WAIT_S` (2 s) i wtedy odmawiają. Tak samo
sprzątanie worktree (`/Worktrees Remove`, `RemoveForced`): katalogi żywych agentów liczy na sondzie nowszej niż
wywołanie (`AgentMonitor.refresh_fresh`), a bez niej odmawia każdej ścieżki (`unavailable`) bez skutku. Wynik sondy jest
wyłącznie prezentacją: przepływ karty (start kroku, odzyskiwanie, zgoda) go nie czyta — decyduje świeży, pełny odczyt
dziennika w kolejce mutacji, więc uszkodzona historia i nieznana tożsamość odmawiają bez skutku także wtedy, gdy sonda
widzi żywy proces (`daemon/tests/test_monitor_probe.py`).

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
| `Open(s path, s app)` | `→ b` | Otwiera worktree: `app` = `terminal` (`konsole --workdir`) albo `rider`. Tylko ścieżki z listy; dla `rider` przyjmowany jest też istniejący zwykły plik wewnątrz wylistowanego worktree (po `realpath`; PT-361, „Otwórz w edytorze” z widoku diffa), `terminal` nadal tylko katalog |
| `Launchers()` | `→ s` | JSON `{"terminal": bool, "rider": bool, "window": bool}` — które przyciski pokazać; `window`: czy okno Worktrees jest zainstalowane |
| `ShowWindow()` | `→ b` | Otwiera okno „Tide Worktrees” (drugie uruchomienie tylko je pokazuje). `false`, gdy okno nie jest zainstalowane |
| `Prune(s repoPath)` | `→ s` | `git worktree prune` w znanym repozytorium, które ma worktrees bez katalogu. JSON: `repoPath`, `ok`, `pruned` (ścieżki), `message`. Odpowiedź po zakończeniu |
| `Fetch(s repoPath)` | `→ s` | „Pobierz” przy repozytorium: `git fetch <zdalny>` dla każdego zdalnego z konfiguracji. JSON `{ok, repoPath, remotes, newCommits, message}` albo `{ok: false, reason, message, …}` (opis i błędy niżej). Odpowiedź po zakończeniu (do 120 s), potem usługa odświeża listę (nowe liczby ahead/behind, `lastFetch`) |
| `PullFastForward(s path)` | `→ s` | „Zaciągnij” przy worktree: przesunięcie jego gałęzi do upstreamu **wyłącznie fast-forward** (`merge --ff-only`), pod blokadą repozytorium scalania karty. JSON `{ok, path, branch, upstream, from, to, commits, message}` albo `{ok: false, reason, message, path, …}` (opis i kody odmów niżej). Odpowiedź po zakończeniu (do 10 s czekania na blokadę i 60 s gita), potem usługa odświeża listę |
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
| `flowBranch` | bool | gałąź worktree jest gałęzią karty albo ticketu karty w przepływie (dziennik kart: zamiar albo wynik `card-branch`, `ticket-branch`, `review-branch`, `step-reopen` i gałęzie generacji zmiany bazy; karta po starcie, przed `card.finished` i scaleniem do głównej). Liczone przy odświeżeniu listy; błąd odczytu dziennika → `false` (podpowiedź dla okna — `PullFastForward` czyta dziennik na nowo i wtedy odmawia) |
| `dirty` | int | liczba pozycji w `git status --porcelain` (nieśledzony katalog to jedna pozycja); `-1`, gdy statusu nie dało się odczytać (traktowane jak zmiany) |
| `mainBranch` | string | lokalna gałąź główna: `master`, `main` albo `develop` (ta, na której stoi główny katalog, inaczej pierwsza istniejąca w tej kolejności); pusty, gdy żadnej nie ma |
| `merged` | bool | czubek worktree jest przodkiem lokalnej gałęzi głównej (`unmerged == 0`) |
| `unmerged` | int / null | `git rev-list --count <główna>..HEAD`; `null` bez gałęzi głównej |
| `unpushed` | int | commity tylko w tym worktree: osiągalne z HEAD, a z żadnej innej lokalnej gałęzi ani gałęzi zdalnej (`rev-list --count HEAD --not --exclude=<gałąź> --branches --remotes`, stan z ostatniego fetch, bez sieci). To one przepadają po `branch -D` albo z odłączonym HEAD. Liczone tylko dla niescalonych (inaczej `0`); `-1`, gdy nie dało się policzyć |
| `upstream` | string | skrót upstreamu gałęzi (`origin/master`, z `for-each-ref`, jedno wywołanie na repozytorium); pusty: gałąź bez upstreamu („tylko lokalnie”) albo odłączony HEAD |
| `upstreamAhead`, `upstreamBehind` | int / null | `git rev-list --left-right --count HEAD...<upstream>` (jedno wywołanie na gałąź): commity gałęzi poza upstreamem i upstreamu poza gałęzią, stan z ostatniego fetch (bez sieci). `null` bez upstreamu albo gdy jego ref zniknął (upstream skasowany lokalnie) |
| `mainAhead`, `mainBehind` | int / null | to samo względem lokalnej gałęzi głównej (`mainBranch`, wybranej jak dotąd); `null` bez gałęzi głównej. Liczone też dla odłączonego HEAD i brakującego katalogu (z czubka wpisu worktree); `mainAhead` równa się `unmerged` |
| `remotes` | string[] | pola repozytorium (powtórzone w każdym worktree tego repozytorium): nazwy zdalnych z lokalnej konfiguracji (`config --local --list --name-only`, nie `git remote`); pusta lista: repozytorium bez zdalnego |
| `lastFetch` | ISO 8601 / pusty | pole repozytorium: mtime `FETCH_HEAD` we wspólnym katalogu gita; pusty napis, gdy nigdy nie pobrano (albo repozytorium nie ma własnego katalogu `.git`) |
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

**Pobieranie (`Fetch`, PT-358).** Jedyna metoda obszaru, która używa sieci i jedyne polecenie zapisujące tego obszaru poza sprzątaniem: zwykły `git fetch --no-prune --no-recurse-submodules -- <zdalny>` dla każdego zdalnego z lokalnej konfiguracji (`remotes`, kolejno). Zapisuje `refs/remotes/…` i `FETCH_HEAD` (z niego `lastFetch`); nie ma `--prune` (zdalnie usunięta gałąź zostaje w `refs/remotes`; także gdy użytkownik ma `fetch.prune=true`), `--force`, `--tags` ani refspeca od Tide, nie rusza HEAD, indeksu, gałęzi lokalnych ani worktrees, żadnego push, rebase, reset, stash. Inne reguły:

- **Tylko repozytorium z listy.** `repoPath` porównujemy po ścieżce kanonicznej (`realpath`: dowiązania i `..`) z repozytoriami z `List()`; inna ścieżka, pusty napis, ścieżka względna → `bad-request` bez uruchomienia gita. Do gita idzie ścieżka z listy, nie ta z żądania. Repozytorium bez zdalnego → `no-remote` (okno nie pokazuje wtedy przycisku).
- **Bez blokady silnika (review K-36, M1).** Fetch zapisuje wyłącznie `refs/remotes/*` i `FETCH_HEAD`, które silnik przepływu karty tylko czyta, więc nie bierze blokady repozytorium scalania karty (`cardflow.gitops.lock_for`). Silnik bierze ją blokująco w wątku głównym (`StartCard`, `MergeCardToMain`, podgląd zmiany bazy) — trzymanie jej przez transfer sieciowy (do 120 s) zatrzymywałoby całą usługę. Zostaje blokada „jedno pobieranie na repozytorium” (`fetch.fetch_lock`, klucz: ścieżka kanoniczna, bez czekania): dwa żądania dla tego samego repozytorium naraz (także podanego przez dowiązanie) — drugie dostaje `busy`. Praca idzie w osobnym wątku, więc odświeżanie listy i sprzątanie nie czekają na sieć.
- **Bez pytań.** `GIT_TERMINAL_PROMPT=0`, a `GIT_ASKPASS` i `SSH_ASKPASS` ustawione na pusty napis (samo `GIT_TERMINAL_PROMPT=0` nie powstrzymałoby gita przed uruchomieniem programu z otoczenia użytkownika), SSH w trybie wsadowym (`GIT_SSH_COMMAND` = dotychczasowe polecenie SSH — z otoczenia, `core.sshCommand` albo `ssh` — plus `-o BatchMode=yes`). Brak klucza albo hasła kończy się błędem `auth`, nie pytaniem. Poświadczenia są użytkownika (agent SSH, pomocnik poświadczeń gita z jego konfiguracji — usługa działa poza piaskownicą). Bez powłoki z naszej strony, hooki i `fsmonitor` wyłączone jak przy innych wywołaniach gita, `protocol.ext.allow=never`, bez sprzątania w tle (`maintenance.auto=false`, `gc.auto=0`). Repozytorium objęte granicą Gita (`gitcommand.boundary_active`: tryb pudełek włączony albo repozytorium oznaczone/wystawione pudełku) nie jest pobierane: git rusza tam w piaskownicy bez sieci i kluczy, a `.git/config` jest zapisywalny z pudełka (`core.sshCommand`, `credential.helper`, `uploadpack`), więc fetch z poświadczeniami użytkownika wykonałby cudzą konfigurację. Wynik to `boundary` bez uruchomienia `git fetch`; pobieranie w takich repozytoriach wymaga osobnej decyzji właściciela i utwardzenia.
- **Limit 120 s** na całe pobieranie (`FETCH_TIMEOUT`); po nim proces gita jest zabijany, a wynik to `timeout`.
- **Poświadczenia z adresów nie wychodzą.** Komunikaty, wynik i log usługi przechodzą przez `mask_credentials`: `https://user:token@host/…` → `https://***@host/…`. Komunikat błędu to jedno zdanie po polsku z nazwą zdalnego i pierwszą (zamaskowaną) linią gita.

Wynik: `ok`, `repoPath`, `remotes` (po jednym na zdalny: `name`, `ok`, `reason` przy błędzie, `newCommits`, `branches`: `{branch, ref, new, commits}` — zmienione gałęzie zdalne; `commits` to commity osiągalne z nowego czubka, których nie było w żadnej gałęzi ani gałęzi zdalnej przed pobraniem, `null`, gdy nie dało się policzyć), `newCommits` (suma), `message` (np. `pobrano: origin — 3 nowe commity na master`, `pobrano: origin — brak nowych commitów`). Przy błędzie któregokolwiek zdalnego `ok: false`, `reason` pierwszego błędu i `message` z jego opisem (reszta zdalnych jest mimo to pobierana, a ich nazwy dopisane w nawiasie).

| `reason` | Znaczenie |
| --- | --- |
| `bad-request` | ścieżka spoza listy Worktrees (dowiązanie, `..`, pusta, względna) — git nie został uruchomiony |
| `no-remote` | repozytorium nie ma zdalnego |
| `boundary` | repozytorium objęte granicą Gita (tryb pudełek albo repozytorium wystawione pudełku) — git nie został uruchomiony |
| `busy` | pobieranie tego repozytorium już trwa |
| `auth` | uwierzytelnienie, klucz SSH, host key — bez pytania o hasło |
| `network` | nie można połączyć się ze zdalnym (adres, DNS, połączenie) |
| `timeout` | przekroczono 120 s |
| `git` | inny błąd gita (np. zdalny wskazuje nieistniejące repozytorium) |
| `unavailable`, `error` | usługa się zamyka / wyjątek w usłudze |

W oknie Worktrees pasek repozytoriów na górze listy ma wiersz na repozytorium (zdalne i „pobrano …”) z przyciskiem „Pobierz” — tylko przy repozytorium ze zdalnym; w trakcie „Pobieram…” (wyłączony), wynik albo błąd (jedno zdanie) zostaje pod wierszem do następnego pobrania.

**Zaciąganie (`PullFastForward`, PT-353).** Jedyny zapis HEAD w obszarze: `git merge --ff-only --no-autostash --no-stat <SHA>` w danym worktree, gdzie `<SHA>` to `@{upstream}` bieżącej gałęzi odczytany pod blokadą tuż przed scaleniem (stan sprawdzony = stan scalony, nawet gdy równoległe „Pobierz” przesunie `refs/remotes/…` w międzyczasie). Tide nigdy nie scala historii ani nie robi rebase za użytkownika (historie 27–29): gdy fast-forward nie jest możliwy, odpowiada odmową z kodem i jednym zdaniem, a żadna odmowa nie zmienia HEAD ani drzewa. Brak `--force`, `reset`, `rebase`, `pull`, `push`, `stash`, `prune`, `checkout`; `merge.autoStash=false`, `submodule.recurse=false`, bez sprzątania w tle. Sieci nie ma (zaciąga się stan z ostatniego „Pobierz”). Inne reguły:

- **Tylko worktree z listy.** `path` porównujemy po ścieżce kanonicznej (`realpath`: dowiązania i `..`) z worktrees z `List()` (bez brakujących katalogów); inna ścieżka, pusta, względna, nie-napis → `bad-request` bez uruchomienia gita. Do gita idzie ścieżka z listy. Gałąź główna w głównym katalogu jest dozwolona, jeśli jest czysta.
- **Wspólna blokada ze scalaniem karty.** Ta sama blokada repozytorium co scalanie karty (`cardflow.gitops.lock_for`, import bez zmian): czekanie krótkie (`pull.LOCK_WAIT` = 10 s, potem `locked`) i wyłącznie w wątku roboczym obszaru („Zaciągnij” ma własny wątek — nie stoi za pobieraniem ani odświeżaniem listy; wątek główny usługi niczego nie czeka). Blokada jest trzymana tylko na lokalne odczyty i `merge` (bez sieci), zwalniana w `finally` także po wyjątku i po limicie czasu gita (`pull.PULL_TIMEOUT` = 60 s na `merge` z checkoutem plików). Fetch nie bierze tej blokady, więc nie ma zagnieżdżania ani zakleszczenia z „Pobierz”. Drugie żądanie dla worktree, który właśnie zaciągamy, dostaje `locked` bez czekania.
- **Świeży stan pod blokadą, tuż przed `merge`.** Gałąź (`symbolic-ref`), trwająca operacja gita, upstream i jego SHA, HEAD, `git status` (także pliki nieśledzone), ahead/behind (`rev-list --left-right --count`) i filtry plików są czytane po wzięciu blokady — dane listy służą tylko oknu.
- **Dziennik kart przed blokadą.** Gałąź karty albo ticketu karty w przepływie (`flowBranch`, te same reguły) rozpoznajemy istniejącymi odczytami dziennika (`Journal.cards`, `Journal.state`) **przed** wzięciem blokady repozytorium: silnik bierze `journal.lock` także wtedy, gdy potem czeka na `lock_for` w wątku głównym, więc odczyt dziennika pod `lock_for` w wątku roboczym odwracałby kolejność blokad. Rozpoznanie jest zachowawcze: gałęzie kart po domknięciu (do scalenia do głównej), scalonych ticketów i zamiarów bez wyniku też się liczą, wpis bez repozytorium pasuje do każdego, a błąd odczytu dziennika to odmowa `card-step`. Żadnych zdarzeń w dzienniku karty.
- **Filtry plików (checkout).** Fast-forward wykonuje checkout zmienionych plików; sterownik `filter.<nazwa>.smudge` albo `.process` (np. git-lfs, który sięga do sieci, albo dowolny program z konfiguracji) uruchomiłby się wtedy pod blokadą. Gdy repozytorium ma taki sterownik w konfiguracji, a któryś zmieniany plik ma ten `filter` (`check-attr` w worktree) albo zmienia się `.gitattributes`, odmowa `git-operation` z nazwą sterownika — zaciągnięcie zostaje użytkownikowi. Przerwany `merge` (limit czasu, błąd) nie jest naprawiany: wynik `timeout` albo `git` opisuje stan po nim (HEAD, liczba zmian w katalogu — spójność po przerwaniu) i radzi `git status`.
- **Środowisko jak przy „Pobierz”.** Bez pytań (`GIT_TERMINAL_PROMPT=0`, puste `GIT_ASKPASS`/`SSH_ASKPASS`, SSH wsadowo), hooki i `fsmonitor` wyłączone jak w każdym wywołaniu gita, `GIT_NO_LAZY_FETCH=1` (częściowy klon nie dociąga obiektów z sieci). Repozytorium objęte granicą Gita → `boundary` bez gita.

Wynik udany: `ok: true`, `path`, `branch`, `upstream` (skrót, np. `origin/master`), `from`, `to` (SHA przed i po), `commits` (ile commitów zaciągnięto), `message` (np. `zaciągnięto 3 commity z origin/master (fast-forward)`). Odmowa: `ok: false`, `reason`, `message`, `path`, a gdy już wiadomo — `branch`, `upstream`, `ahead`, `behind`; po przerwanym `merge` także `head` i `dirty`.

| `reason` | Znaczenie (HEAD i drzewo bez zmian, chyba że niżej inaczej) |
| --- | --- |
| `bad-request` | ścieżka spoza listy Worktrees (dowiązanie, `..`, pusta, względna) — git nie został uruchomiony |
| `agent` | w worktree pracuje agent albo trwa krok/praca karty (migawka z chwili żądania) |
| `card-step` | gałąź karty albo ticketu karty w przepływie („tą gałęzią zarządza Tide”) albo nieczytelny dziennik kart |
| `git-operation` | trwa operacja gita (`MERGE_HEAD`, `rebase-merge`/`rebase-apply`, `CHERRY_PICK_HEAD`, `REVERT_HEAD`, `sequencer`, `BISECT_LOG`, `index.lock`, `HEAD.lock`, blokada refu gałęzi) albo zmiany przechodzą przez filtr plików |
| `no-upstream` | odłączony HEAD, gałąź bez upstreamu albo jego ref zniknął |
| `dirty` | zmiany w katalogu (także pliki nieśledzone) albo statusu nie da się odczytać |
| `diverged` | gałąź ma commity poza upstreamem i upstream poza gałęzią (ahead i behind) — Tide nie scala ani nie robi rebase |
| `up-to-date` | nic do zaciągnięcia (behind 0, także gdy gałąź jest tylko przed upstreamem) |
| `locked` | blokada repozytorium zajęta dłużej niż 10 s (scalanie karty) albo zaciąganie tego worktree już trwa |
| `boundary` | repozytorium objęte granicą Gita — git nie został uruchomiony |
| `timeout`, `git` | `merge` przekroczył 60 s albo git odmówił; komunikat opisuje stan po nim (HEAD, zmiany) |
| `unavailable`, `error` | usługa się zamyka / wyjątek w usłudze |

W oknie Worktrees wiersz worktree z upstreamem ma „Zaciągnij ↓N” (gałąź główna głównego katalogu — w pasku repozytoriów, „Zaciągnij ↓N master”). Gdy z danych listy wynika odmowa, przycisk jest wyłączony, a obok stoi powód: „pracuje agent”, „trwa krok karty”, „tą gałęzią zarządza Tide”, „brak upstreamu”, „zmiany w katalogu”, „rozjechana z … — scal albo zrób rebase sam”, „gałąź aktualna”. W trakcie „Zaciągam…” (wyłączony, bez drugiego żądania); wynik albo odmowa (jedno zdanie) zostaje pod wierszem, a lista odświeża się po odpowiedzi.

Zasady sprzątania (`Remove`):

- **Bez sieci.** Żadnego `fetch`/`pull` (`Fetch` wyżej to osobna, wywoływana przez użytkownika akcja) — „scalony” znaczy scalony z *lokalną* gałęzią główną. Gałąź wypchnięta i scalona tylko na serwerze liczy się jako niescalona.
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

## Interfejs Git

Odczyty gita jako dane (K-36): „co zmieniła ta karta” bez UI. Obiekt `/Git` obok `/Worktrees`
(`dbus_service.GitAdaptor`, `git_monitor.py`, moduł `daemon/tide_daemon/gitread/`).

| Element | Wartość |
| --- | --- |
| Obiekt | `/Git` |
| Interfejs | `org.tide.Daemon.Git` |

| Składnik | Sygnatura | Opis |
| --- | --- | --- |
| `Diff(s request)` | `→ s` | Diff jako dane. `request` to obiekt JSON ze źródłem (niżej). Odpowiedź `{ok: true, …DiffData}` albo `{ok: false, error, message}`; opóźniona — git działa w puli wątków runnera odczytu (`GitReader.submit`), pętla usługi w tym czasie obsługuje inne wywołania |

**Źródło `card`** (PT-357): `{"source": "card", "card": "K-N", "targetRef": "refs/heads/master"}` (`targetRef`
opcjonalny; pusty napis = brak). Działa w każdym stanie karty, która ma gałąź (także po domknięciu i scaleniu,
dopóki gałąź karty istnieje).

- Bez `targetRef` zakres zaczyna się od przypiętego `baseCommit` z dziennika karty i kończy na gałęzi karty
  („zmiany od startu”). Ruch gałęzi startowej po starcie (np. nowe commity na `master`) nie zmienia początku zakresu.
- Z `targetRef` zakres zaczyna się od merge-base celu i gałęzi karty. Ref może być pełny (`refs/heads/main`) albo
  krótki (`main`); waliduje go runner odczytu (ref zaczynający się od `-` to `bad-request`, git nie rusza).
- Repozytorium karty pochodzi wyłącznie z dziennika (`card.started.repo`, zapis przy starcie zlecenia; karta z grupy
  kart — repozytorium jej projektu). Żądanie nie podaje ścieżek. Stan karty czytają istniejące funkcje odczytu
  (`Journal.has`, `Journal.state`, `state.base_of`, `state.ticket_of`); odczyt nie dopisuje zdarzeń do dziennika
  i nie zmienia refów.
- Wszystkie polecenia gita (`rev-parse`, `merge-base`, `diff`, `log`) idą przez utwardzony runner odczytu
  (`gitread/runner.py`: bez haków, filtrów, textconv, zewnętrznego diffu, pagera i gpg z konfiguracji repozytorium,
  bez sieci); diff liczony jest między dwoma SHA (`--no-renames`, kontekst 3 linie), tekst parsuje
  `gitread/diffparse.py`.
- `CardBranchDiff` na `/Board` zostaje bez zmian (tekst diffu do oceny domknięcia).

**Źródło `ticket`** (PT-349): `{"source": "ticket", "card": "K-N", "ticket": "PT-N"}`. Zmiany jednego ticketu karty.

- Zakres: od merge-base gałęzi karty i gałęzi ticketu do gałęzi ticketu (`from.ref` — gałąź karty, `to.ref` —
  `refs/heads/<gałąź ticketu>`), więc drugi ticket nie zawiera plików pierwszego. Tytuł: `PT-12 · zmiany ticketu
  względem gałęzi karty`; odpowiedź ma dodatkowe pole `ticket`.
- Repozytorium pochodzi wyłącznie z dziennika karty (jak w źródle `card`); `repo` w żądaniu jest ignorowane.
  Ticket musi być znany dziennikowi karty (rezerwacja, gałąź, krok, przydział albo scalenie), inaczej `bad-request`.
- **Ticket scalony, gałąź usunięta.** Gdy gałęzi ticketu nie ma — albo jest, ale ticket jest już w gałęzi karty
  (zakres byłby pusty) — zakres bierze się z pary zamiar/wynik akcji `merge` w dzienniku, czytanej istniejącymi
  funkcjami odczytu stanu: `intent` z `expectedTip` (zatwierdzony czubek ticketu) i `cardTip` (czubek gałęzi karty
  przed scaleniem) oraz `result` z `ok: true`. Zakres: merge-base(`cardTip`, `expectedTip`) → `expectedTip`
  (`from.ref`/`to.ref` to wtedy SHA). Bez takiego zapisu (ticket niescalony bez gałęzi, ticket przejęty z planu,
  stary dziennik), z nieudanym scaleniem albo z SHA, którego nie ma w repozytorium, odpowiedź to
  `{ok: false, error: "no-ticket-range", message}` z jednym zdaniem, co dalej („Gałąź ticketu PT-12 nie istnieje,
  a dziennik karty nie zapisał zakresu jego scalenia.”).

**Źródło `commit`** (PT-349): `{"source": "commit", "repo": "/ścieżka", "sha": "58a3f26…"}`. Zakres: pierwszy rodzic →
commit (commit scalający względem pierwszego rodzica, commit początkowy względem pustego drzewa: `from.ref` to
`empty-tree`, `from.sha` — SHA pustego drzewa, `from.date` pusta). Tytuł: `commit 58a3f26 — temat`. `sha` to SHA albo
dowolny ref rozwiązujący się do commita.

**Źródło `range`** (PT-349): `{"source": "range", "repo": "/ścieżka", "from": "master", "to": "feat/x"}`. Zakres od
merge-base(`from`, `to`) do `to`, jak `git diff from...to` (zmiany na `from` po rozejściu nie wchodzą do wyniku);
`from`/`to` to pełne refy, krótkie nazwy albo SHA. `from.sha` w odpowiedzi to merge-base. Tytuł: `<to> względem
<from>` (np. `feat/x względem master`). Brak wspólnej historii to `bad-request`. Używa go „Zmiany względem głównej”
w Worktrees (PT-360): `repo` to repozytorium projektu, `from` — `refs/heads/<lokalna główna projektu>`, `to` —
`refs/heads/<gałąź worktree>`.

**Źródło `worktree`** (PT-360): `{"source": "worktree", "path": "/ścieżka/worktree"}` — niezacommitowana praca katalogu,
bez terminala. `path` trafia do tej samej funkcji walidacji co `repo` (niżej): musi być worktree z listy Worktrees
(albo repozytorium projektu Tablicy), po ścieżce kanonicznej, bez dowiązań i `..`; inaczej `bad-request` bez
uruchamiania gita na tej ścieżce. Odpowiedź to `DiffData` z dwiema grupami plików i listą nieśledzonych:

- `groups[]` — `{id, name, totals}` w stałej kolejności: `staged` („W indeksie”, `git diff --cached`: indeks względem
  HEAD) i `unstaged` („Poza indeksem”, `git diff`: drzewo robocze względem indeksu); `totals` to `{files, additions,
  deletions}` grupy. Każdy wpis `files[]` ma pole `group` (`staged` albo `unstaged`); pliki są ułożone grupami
  (najpierw `staged`), a ten sam plik zmieniony w obu miejscach występuje dwa razy — raz w każdej grupie, z różnymi
  hunkami. Zamiast kopiować pliki do `groups[].files` (podwoiłoby to odpowiedź), grupa wskazuje swoje pliki przez
  `group`.
- `untracked[]` — ścieżki nieśledzonych plików (po jednej na plik, także w nieśledzonych katalogach; `status
  --porcelain=v1 -z --untracked-files=all`), **bez treści**. Lista ma limit 2000 plików i 1 MB wyniku gita; po
  przekroczeniu w `warnings[]` pojawia się ostrzeżenie. Pliki z `.gitignore` nie są wymieniane.
- `title` — `Niezacommitowane zmiany: <ścieżka kanoniczna>`; `repo` — ta ścieżka; `from` — `{ref: "HEAD", sha, date}`
  (`sha` i `date` puste dla gałęzi bez commitów, wtedy grupa `staged` jest liczona względem pustego drzewa), `to` —
  `{ref: "worktree", sha: "", date: ""}`. `totals` to suma plików obu grup (plik w obu grupach liczy się dwa razy).
- Limity „Duże diffy” działają osobno dla każdej grupy; ostrzeżenia mają przedrostek nazwy grupy (`W indeksie: …`),
  a `truncated` jest prawdą, gdy przycięto którąkolwiek. Rozmiary plików binarnych (`oldSize`/`newSize`) nie są
  liczone dla tego źródła — widok pokazuje wtedy komunikat bez liczb.
- `options.path` z opcjonalnym `options.group` (`staged`/`unstaged`) zwraca treść jednego pliku jednej grupy (bez
  `group` — pierwszej grupy, w której plik ma zmiany: najpierw `staged`); odpowiedź ma pole `group`, a wpis pliku
  ma `group`. Zły `options.group` to `bad-request`. Pozostałe źródła ignorują `group`.
- Odczyt **nie zmienia** worktree: runner uruchamia gita z `GIT_OPTIONAL_LOCKS=0` i `diff.autoRefreshIndex=false`,
  więc `status` i `diff` nie odświeżają ani nie zapisują indeksu, refy i pliki zostają bez zmian, a do dziennika karty
  nic nie trafia. Źródło nie czyta stanu karty ani dziennika — wystarcza `path` z listy Worktrees.
- Katalog worktree, którego już nie ma (usunięty po odczycie listy — historia 17), to błąd `no-worktree` z komunikatem
  „Katalog worktree nie istnieje (został usunięty?): <ścieżka>”, bez uruchamiania gita.

Odbiorcy bez obsługi grup (źródła `card`, `ticket`, `commit` i `range`) nie dostają pól `groups[]` i `untracked[]`
i działają bez zmian; widok diffa traktuje brak `groups[]` jak dotąd (sama lista plików).

**Walidacja wejścia `commit`, `range` i `worktree`** (granica uprawnień; jedna funkcja modułu, `gitread/validate.py`
— `known_repo` — którą `path` źródła `worktree` (PT-360) wywołuje tak samo jak `repo`, bez drugiej kopii; użyje jej
też K-99 dla `Git.Commits` i `Git.BranchMap`):

- `repo` (dla `worktree` — `path`) musi być ścieżką bezwzględną bez składników `..` i znaków sterujących, a po rozwiązaniu do ścieżki
  kanonicznej (`realpath`, bez dowiązań) równą repozytorium projektu Tablicy (`repo` z projektu) albo worktree
  z listy Worktrees (porównywane także po rozwiązaniu). Git działa potem na ścieżce kanonicznej. Dowiązanie
  wskazujące poza listę, ścieżka z `..` i dowolny inny katalog dają `bad-request` — bez uruchamiania gita.
  Lista powstaje przy każdym żądaniu (`GitMonitor.repos_source`); gdy jej nie da się ustalić, żadne `repo` nie jest znane.
- `sha`, `from`, `to` muszą być niepustymi napisami bez znaków sterujących, nie mogą zaczynać się od `-` (nie są
  przekazywane gitowi jako opcje) i muszą rozwiązać się do commita
  (`rev-parse --verify --end-of-options <x>^{commit}`). Inaczej `bad-request`.
- Wszystkie trzy źródła przyjmują `options.path` i `options.context` (jak `card`), mają te same limity i pola
  `DiffData`; `source`, `title`, `repo`, `from`, `to` mają znaczenie jak wyżej, a pole `ticket` pojawia się tylko
  w źródle `ticket` (zmiana addytywna).

**`DiffData`** (kontrakt wspólny dla kolejnych źródeł i widoku diffa; zmiany tylko addytywne):

| Pole | Typ | Opis |
| --- | --- | --- |
| `source` | string | źródło żądania (`card`, `ticket`, `commit`, `range`, `worktree`) |
| `card` | string | `K-N` (znormalizowany identyfikator karty); tylko źródła `card` i `ticket` |
| `ticket` | string | `PT-N`; tylko źródło `ticket` (PT-349) |
| `repo` | string | ścieżka repozytorium: z dziennika karty (`card`, `ticket`) albo kanoniczna zwalidowana (`commit`, `range`, `worktree`) |
| `title` | string | krótki opis zakresu, np. `K-36: zmiany gałęzi card/k-36-… od startu` |
| `from`, `to` | object | `{ref, sha, date}`: `ref` — `baseRef` z dziennika albo `targetRef` (dla `to` — `refs/heads/<gałąź karty>`), `sha` — pełny SHA początku/końca zakresu, `date` — data commitera w ISO 8601 UTC (`2026-11-11T10:00:00Z`). Surowe dane: etykietę „Start od…” formatuje UI (`startLabel`) |
| `files` | array | pliki z parsera diffu: `path`, `oldPath`, `status` (`A`, `D`, `R`, `C`, `T`, `M`), `additions`, `deletions`, `binary`, `oldMode`, `newMode`, `warnings`, `hunks` (`oldStart`, `oldLines`, `newStart`, `newLines`, `header`, `lines` — `kind` ` `/`-`/`+`, `oldNo`, `newNo`, `text`); pola z „Duże diffy” niżej pojawiają się tylko przy plikach, których dotyczą |
| `totals` | object | `{files, additions, deletions}` — dla całego zakresu także po przycięciu (liczone z pełnej listy plików) |
| `truncated` | bool | `true`, gdy całość przekroczyła limit i treść części plików jest pominięta (niżej); inaczej `false` |
| `warnings` | array | ostrzeżenia: parsera (tekst przed pierwszym nagłówkiem) i limitów (przycięcie) |
| `groups` | array | tylko źródło `worktree` (PT-360): grupy plików `{id, name, totals}` (`staged` „W indeksie”, `unstaged` „Poza indeksem”); wpis `files[]` wskazuje grupę polem `group` |
| `untracked` | array | tylko źródło `worktree` (PT-360): ścieżki nieśledzonych plików, bez treści |

**Duże diffy** (PT-355; pola tylko dodane do `DiffData`):

- **Limit całości:** 2 MB tekstu diffu albo 20 000 linii — co pierwsze. Limit jest liczony przy czytaniu wyjścia
  gita (strumieniowo, `GitReader.run(max_bytes, max_lines)`): po przekroczeniu proces gita jest przerywany, a nadmiar
  nigdy nie trafia do pamięci. Wynik to `truncated: true` i ostrzeżenie w `warnings[]` („Diff przekracza limit …:
  pokazano część zmian (N z M plików z treścią). Wybierz plik, żeby doczytać jego zmiany.”). **Lista wszystkich plików
  jest zawsze pełna** — po przycięciu usługa dokłada ją z `git diff --raw` i `--numstat` (bez treści), więc
  `files[]` i `totals` obejmują cały zakres. Plik, którego treść nie zmieściła się w limicie w całości, ma
  `truncated: true` i `hunks: []`; pliki przeczytane w całości zachowują hunki.
- **Plik `large`:** plik z ponad 3 000 zmienionych linii (`additions + deletions`) ma `large: true`, `lines` (liczba
  zmienionych linii) i `hunks: []` — także gdy diff mieści się w limicie całości. `large` wypiera `truncated`.
- **Plik binarny:** `binary: true` (bez hunków) oraz `oldSize` i `newSize` — rozmiary w bajtach z drzew obu commitów
  (`git ls-tree -l`); `null` po stronie, której nie ma (plik dodany: `oldSize: null`, usunięty: `newSize: null`).
- **`options.path`** (ścieżka pliku względem repozytorium): odpowiedź ma ten sam kształt `DiffData` (to samo
  `from`/`to`), `files` zawiera dokładnie jeden plik **z hunkami** — także `large` i przycięty — a `totals` dotyczą
  tego pliku; dodatkowe pole `path` to echo żądania. Plik bez zmian w zakresie to błąd `unknown-path`. Pojedynczy plik
  ma własny, luźniejszy limit 10 MB albo 100 000 linii; po jego przekroczeniu plik ma `truncated: true`, odpowiedź
  `truncated: true` i ostrzeżenie, a hunki to początek zmian. Widok (#13) wstawia taki plik w miejsce wpisu z listy.
- **`options.context`** (liczba całkowita 0–100000, domyślnie 3): liczba linii kontekstu hunków pliku z `options.path`
  (`git diff --unified=N`), np. 20 do rozwinięcia kontekstu. Wymaga `options.path` (bez niego `bad-request`), bo dotyczy
  jednego pliku. Nieznane klucze `options` są ignorowane.
- Żądanie z `options` ma ten sam wybór źródła (`source` i jego pola) co lista; kontrakt jest identyczny dla źródeł
  `card`, `ticket`, `commit`, `range` (PT-349) i `worktree` (PT-360; tu `options.group` wskazuje grupę pliku).

**Podświetlanie składni w widoku diffa** (K-36, PT-351): linie kodu `.py`, `.qml`, `.js`, `.cs`, `.md`, `.yml`/`.yaml`, `.json`
i `.sh` (język z rozszerzenia, `Diff.languageFor`) dostają kolory składni, jeśli w systemie jest moduł QML
`org.kde.syntaxhighlighting`. Delegat linii ma `SyntaxText.qml`: Loader z lekkim, tylko do odczytu `TextEdit`, tworzonym
wyłącznie dla widocznych linii znanego języka (nie dla pliku `large` ani binarnego, nie dla nagłówków hunków). Podświetlacz
to `Component` wstrzykiwany jednym punktem (`DiffView.highlighter`, w testach atrapa); domyślnie widok ładuje
`ModuleHighlighter.qml` dynamicznie przez `Qt.createComponent` i przy braku modułu zostaje przy zwykłym `Text`, a ściągawka
(`?`) mówi „Podświetlanie składni niedostępne (brak modułu KSyntaxHighlighting)”. Motyw: „Tide”, generowany z palety
(`theme/syntax/generate.py`, instalowany przez `make install-appearance` do `$XDG_DATA_HOME/org.kde.syntax-highlighting/themes/`);
każdy kolor tekstu ma kontrast >= 4,5:1 (WCAG 2.x) na tle okna i na tłach linii dodanej i usuniętej, style nie mają tła
formatu. Bez zainstalowanego motywu `ModuleHighlighter` używa ciemnego motywu modułu. Tła linii dodanej, usuniętej i
kontekstowej się nie zmieniają; `DiffView.readableColor` zastępuje kolor formatu tekstem z palety dla podświetlaczy, które go
używają (atrapa w testach). Ograniczenia: każda linia to osobny dokument, więc wieloliniowe konstrukcje (komentarze blokowe,
napisy wielowierszowe) nie są rozpoznawane; kolorów formatów modułu nie da się podmienić z QML, stąd osobny motyw; motyw
powstaje z Mist (warianty palety nie mają własnego).

**Widok diffa** (`apps/common/qml/git/DiffView.qml`, `diff.js`): plik `large` jest zwinięty z przyciskiem „duży plik —
N linii, pokaż” (klik wysyła `Git.Diff` z `options.path` i wstawia treść); plik binarny pokazuje „plik binarny (rozmiar
przed → po)” bez treści i niczego nie doczytuje; przy `truncated: true` nad treścią jest pasek „Pokazano część zmian;
wybierz plik, żeby doczytać”, a wybór pliku bez treści (`truncated`) sam doczytuje go przez `options.path`. Błąd
doczytania pokazuje komunikat i „Spróbuj ponownie”; „Odśwież” (`r`) czyta całość od nowa i odrzuca spóźnione
odpowiedzi doczytywania.

**Grupy plików i nieśledzone w widoku** (PT-360): przy odpowiedzi z `groups[]` lista plików ma nagłówek każdej
niepustej grupy z liczbą plików i sumą (`W indeksie · 2 pliki · +3 −1`), a pod plikami — nagłówek „Nieśledzone · N plików
(bez treści)” i same ścieżki (zwykły tekst, bez wyboru i bez podglądu). Plik w obu grupach to dwa wiersze; `]`/`[`
chodzą po plikach w kolejności wyświetlania, a doczytanie pliku (`options.path`) wysyła też `options.group`. Sama lista
nieśledzonych (bez plików w grupach) nie jest „brakiem zmian”; czysty worktree pokazuje „Brak niezacommitowanych
zmian.”. Odpowiedź bez `groups[]` wygląda jak dotąd.

**Worktrees** (PT-360): przy worktree z niezacommitowanymi zmianami (`dirty` ≠ 0) okno ma „Zmiany w katalogu” (źródło
`worktree`, `path` worktree), a przy worktree z gałęzią inną niż lokalna główna projektu — „Zmiany względem głównej”
(źródło `range`). Widok otwiera się na całe okno nad listą; Esc albo „Zamknij” wracają do listy. Backend okna wywołuje
`Git.Diff` (`/Git`) metodą `gitDiff(tag, żądanie)` i oddaje odpowiedź sygnałem `replied(tag, json)`.

**Kody błędów** (`{ok: false, error, message}`, `message` po polsku):

| `error` | Kiedy |
| --- | --- |
| `bad-request` | żądanie nie jest obiektem JSON, nieznane `source`, brak albo zły identyfikator karty, `options` nie jest obiektem, zły `options.path`/`options.context` (także `context` bez `path`), `targetRef` nie jest napisem, nie istnieje albo nie ma wspólnej historii z gałęzią karty; źródło `ticket`: zły albo nieznany karcie ticket; `commit`/`range`: `repo` spoza projektów Tablicy i listy Worktrees (także przez dowiązanie i `..`), ref zaczynający się od `-` albo niebędący commitem, `from` i `to` bez wspólnej historii (PT-349); `worktree`: `path` spoza listy Worktrees (te same reguły co `repo`) albo zły `options.group` (PT-360) |
| `unknown-card` | karta nie ma dziennika przepływu albo nie była zlecona (brak `card.started`) |
| `no-branch` | karta nie ma gałęzi (brak wyniku `card-branch`, brak repozytorium w dzienniku, katalog repozytorium albo gałąź karty nie istnieje) |
| `unknown-base` | bez `targetRef`, gdy baza startu w dzienniku jest nieznana albo jej commita nie ma w repozytorium; komunikat prosi o jawny ref porównania (`targetRef`) |
| `unknown-path` | `options.path` wskazuje plik bez zmian w zakresie diffu (PT-355) |
| `no-ticket-range` | źródło `ticket`: gałęzi ticketu nie ma, a dziennik karty nie zapisał zakresu jego scalenia (albo zapisane SHA nie istnieje w repozytorium); `message` mówi jednym zdaniem, co dalej (PT-349) |
| `no-worktree` | źródło `worktree`: `path` jest na liście Worktrees, ale katalogu już nie ma (usunięty po odczycie listy); `message`: „Katalog worktree nie istnieje (został usunięty?): <ścieżka>” (PT-360) |
| `unavailable` | przepływ karty jest wyłączony w usłudze (brak dziennika kart) albo nie da się uruchomić gita |
| `timeout`, `cancelled`, `unsafe-config`, `failed` | błędy runnera odczytu: limit czasu (30 s), zamykanie usługi, sterownik `filter.*` o nazwie, której runner nie umie wyłączyć, inny błąd gita |

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
- **skille użytkownika (PT-216):** `tasks/skills.py` szuka `SKILL.md` w `~/.claude/skills` dla Claude
  oraz `~/.agents/skills` i `~/.codex/skills` dla Codexa (pierwsze źródło wygrywa przy powtórzonej nazwie).
  Obsługuje symlinki, także gdy cały katalog skilli wskazuje poza `~/.claude`, i katalogi zagnieżdżone.
  `in/skills.json` zapisuje listę nazw i rzeczywistych ścieżek. Ta sama lista trafia na koniec polecenia
  kroku, także przy wznowieniu. Brak skilla wymaganego przez polecenie kończy się jawnym `result=error`
  z jego nazwą, zamiast udawania wykonania procedury,
- Claude zachowuje `--setting-sources=` i `--strict-mcp-config`. Samo `--add-dir` nie przywraca skilli:
  ich ładowanie z dodatkowych katalogów zależy od źródła ustawień `project`.
  Tide generuje pod `in/skills-plugin-<odcisk>` lokalny plugin i podaje go przez `--plugin-dir`.
  Zawiera wyłącznie nazwę, opis oraz instrukcję odczytania oryginalnego `SKILL.md`; nie kopiuje
  hooków, `allowed-tools`, `context: fork`, plików ustawień, MCP ani pluginów użytkownika.
  W `init`/liście poleceń skille mają nazwy `tide-user-skills:to-spec`, `tide-user-skills:tdd` itd.;
  polecenie kroku wskazuje to mapowanie. Cele symlinków dostają `sandbox.filesystem.allowRead`,
  `denyWrite` i reguły narzędzi `Read`/zakaz `Edit`; nie są dodawane do zapisywalnych katalogów sesji.
  Zasoby względne skilla należy czytać przy oryginalnym `SKILL.md`, a zasady kroku mają pierwszeństwo,
- Codex wykrywa natywnie skille z `~/.agents/skills`; zgodność ze starszym `~/.codex/skills` zapewnia
  jawna lista ścieżek w poleceniu. Wersja bez natywnego wykrywania ma przeczytać wskazany `SKILL.md`
  i jego zasoby bezpośrednio. Nie dodajemy katalogów skilli do `writable_roots`, nie włączamy MCP
  ani dodatkowych źródeł konfiguracji. Nie zakładamy zdarzenia `init` z listą skilli w `codex exec --json`:
  dowodem przekazanej listy są `in/skills.json` i `in/prompt.txt`.
  Natywne ładowanie Claude sprawdza opcjonalny test
  `TIDE_SKILLS_CLAUDE_BIN=/ścieżka/do/claude python3 -m unittest daemon.tests.test_background_skills_cli.NativeSkillsTest -v`:
  używa atrap HOME, hooka i MCP, nie łączy się z modelem, kończy proces po `init`,
  zapisuje surowy `step.log` w `build/test-results/skills-init/` i sprawdza pięć wymaganych skilli.
  Domyślne testy `test_background_skills.py` nie wymagają programu ani modelu.
  Źródła mechanizmów: [źródła skilli Claude](https://code.claude.com/docs/en/skills#load-skills-from-a-directory-outside-the-project),
  [lokalny plugin w CLI Claude](https://code.claude.com/docs/en/cli-reference),
  [katalogi skilli Codexa](https://learn.chatgpt.com/docs/build-skills#where-codex-loads-local-skills).
  Na tej bazie nie ma jeszcze pudełka testowego K-38: zmiana obejmuje aktualną piaskownicę kroków;
  ewentualne późniejsze pudełko musi zachować odczyt wskazanych instrukcji i zasobów,
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

## Zatrzymanie i odtworzenie po restarcie (PT-253)

SIGTERM prosi Qt o zakończenie pętli; budzik Pythona daje obsłudze sygnału sterowanie co 500 ms.
Powrót z `app.exec()` i nawet z `app.main()` nie oznacza końca procesu: Python przy wyjściu
dołącza wątki `ThreadPoolExecutor`, także po `shutdown(wait=False)`. Kontrolowany wolny
`git worktree list --porcelain` odtworzył przekroczenie 5 s: wątek `tide-worktrees` czekał w
`subprocess.run/communicate/select`, a główny w `_python_exit/threading.join`, już po powrocie
z `app.main()`. Limit każdego polecenia wynosił 60 s, a skaner mógł przejść do kolejnego
repozytorium. To odtworzenie mechanizmu dojścia do limitu zatrzymania systemd; nie identyfikuje
konkretnego wolnego repozytorium z wcześniejszego zatrzymania na pulpicie.

Monitor worktrees po rozpoczęciu zamykania odrzuca nową pracę, anuluje kolejkę i sygnalizuje
anulowanie skanerowi. Bieżący git ma własną grupę procesów; anulowanie jest sprawdzane co 50 ms,
następnie grupa dostaje SIGTERM, a najpóźniej po 200 ms SIGKILL. Dotyczy to wyłącznie gita
uruchomionego przez skaner, wraz z jego pomocnikami. Wątek zostaje dołączony przed końcem usługi.
Przegląd katalogów i pomiar zajętości sprawdzają ten sam znacznik anulowania. Pozostałe użycia
klasy `Git` zachowują dotychczasowe limity; nie ma zmiany `TimeoutStopSec` ani wymuszonego wyjścia
interpretera. Blokujący w jądrze odczyt z niedostępnego systemu plików pozostaje ograniczeniem
anulowania kooperatywnego.

Zamknięcie Tablicy zatrzymuje też jej monitor przepływu: nie przyjmuje nowych akcji, usuwa
oczekujące akcje z kolejki, pozwala bieżącej operacji zostawić trwały zamiar/wynik i commituje
zmiany dziennika bez oczekiwania na callback Qt. Operacje przepływu zachowują dotychczasowe
limity. Dziennik ma kopię usługi zapisaną z `fsync`; po starcie karty i oczekujące decyzje są
projekcją tego dziennika. Zwykły restart nie zapisuje zdarzenia ręcznego przeniesienia do Backlogu.
Procesy kroków i poleceń poza piaskownicą pracują w osobnych jednostkach systemd; zatrzymanie
usługi ich nie zatrzymuje. Nowy proces usługi podejmuje te same jednostki i pliki wyników.

Filtr powiadomień Stream odbiera każdą odpowiedź `Inhibit` tylko raz: `waitForFinished()` podczas
zatrzymania może także dostarczyć callback `finished`. Przy zamykaniu oba odbiory nie mogą osobno
zwolnić tego samego ciasteczka. `stop()` zachowuje jedno synchroniczne `UnInhibit` i odrzuca późne
sygnały aktywności oraz próby ponownego wstrzymania powiadomień. Zwykłe wyjście ze Stream nadal
zwalnia inhibicję asynchronicznie, także gdy odpowiedź przyszła dopiero po wyjściu.

`python3 -m unittest -v daemon.tests.test_daemon_lifecycle` mierzy monotonicznie koniec własnego
procesu po SIGTERM (limit 5 s), restart do działającego D-Bus i aktualnej migawki (limit 10 s),
zachowanie decyzji, wyników i gałęzi oraz brak powtórzenia kroków/scalenia. Scenariusze systemd
używają wyłącznie jednostek `tide-pt253-test-<los>.service` i zakresów z osobnym losowym prefiksem,
prywatnej magistrali oraz tymczasowych HOME/XDG/repozytoriów. Sprawdzają jednocześnie żywy krok
i polecenie poza piaskownicą: grupy cgroup, PID-y, postęp obu procesów po restarcie oraz pojedyncze
podjęcie obu wyników. Brak prywatnego D-Bus lub menedżera użytkownika oznacza pominięcie scenariusza,
a nie dowód poprawnego restartu.

Ręczne sprawdzenie po wdrożeniu na pulpit:

1. Rozpocznij kontrolowany, dłuższy krok w tle oraz na drugiej karcie kontrolowane polecenie
   poza piaskownicą. Zapisz ich PID-y z dzienników procesów, kolumny i dane gałęzi obu kart.
2. Gdy oba procesy pracują, wykonaj poniższy pomiar. Wywołuje dokładnie
   `systemctl --user restart tide-daemon` i sprawdza tożsamość obu żywych procesów po restarcie:

   ```python
   import subprocess, time
   from pathlib import Path
   pids = [int(input("PID kroku: ")), int(input("PID polecenia poza piaskownicą: "))]
   def identity(pid):
       fields = Path(f"/proc/{pid}/stat").read_text().rpartition(")")[2].split()
       assert fields[0] != "Z", f"proces {pid} zakończony"
       return fields[19]  # starttime, bez pomylenia z ponownie użytym PID-em
   before = [identity(pid) for pid in pids]
   started = time.monotonic()
   subprocess.run(["systemctl", "--user", "restart", "tide-daemon"], check=True)
   elapsed = time.monotonic() - started
   assert elapsed < 10, elapsed
   assert [identity(pid) for pid in pids] == before
   print(f"restart: {elapsed:.3f} s; oba procesy żyją")
   ```

3. Potwierdź przyrost dziennika/postęp obu procesów. Odczytaj aktualną migawkę przez
   `qdbus6 org.tide.Daemon /Board org.tide.Daemon.Board.Snapshot`; sprawdź kolumny i dane gałęzi,
   także dla karty czekającej na decyzję i karty Do sprawdzenia. Poczekaj na naturalny koniec obu
   kontrolowanych prac: wynik każdego ma zostać podjęty raz, bez dodatkowego kroku, scalenia ani decyzji.
   Testy automatyczne nie uruchamiają tego polecenia na docelowej usłudze.

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
- **Indeks**: `$XDG_DATA_HOME/tide/search/sessions.json` (`$TIDE_SEARCH_INDEX` w testach), plik 0600 w katalogu 0700. Format 2 (K-98 #5, PT-346) to dziennik JSON Lines: nagłówek, potem rekordy zmian (pozycja, sam czas aktywności, usunięcie, czas indeksu) dopisywane przy zmianie — zmiana jednej sesji dopisuje jedną linię zamiast przepisywać plik; pełny zapis atomowy tylko przy nowym pliku, zmianie modelu lub wymiarów, po nieczytelnym pliku i gdy dziennik urośnie ponad 2× liczbę pozycji + 64 linie. Plik to odtwarzalna pamięć podręczna: stary format, uszkodzona linia albo urwany zapis to jedno pełne przeliczenie bez błędu. Bez tekstów: przy sesji skrót tekstu, czas ostatniej aktywności i znormalizowany wektor (float16, base64 — ok. 2,7 KB na sesję przy 1024 wymiarach). Nagłówek ma model (`bge-m3:latest`) i liczbę wymiarów — inny model roli `embed` albo inne wymiary to indeks od nowa. Granice: sesje z okna KRunnera (30 dni), najwyżej 2000 najświeższych.
- **Kiedy liczy**: wątek `tide-search-index` — pierwszy przebieg 60 s po starcie (`--search-delay`), potem co 5 min i od razu po każdym zapisie ustawień modeli (włączenie, wyłączenie, inny model). Przyrostowo: tekst i skrót dokumentu liczone tylko dla sesji ze zmienionymi metadanymi (przebieg bez zmian nie buduje żadnego dokumentu i nie zapisuje pliku), do modelu tylko nowe i zmienione sesje, partiami po 16, z priorytetem `queue.IDLE` (najniższy — rusza, gdy na serwerze nic innego nie czeka). Na karcie 8 GB załadowanie bge-m3 wypycha qwen3:8b (sprawdzone na Ollamie tego komputera), więc przebieg z czymś do policzenia — i każda jego kolejna partia — czeka też na 3 min ciszy po ostatnim wywołaniu innym modelem — inaczej następny opis karty ładowałby swój model od nowa. Partia, która czeka w kolejce dłużej niż 5 min — następna próba za minutę.
- **Zapytanie**: tylko od 3 słów i 12 znaków i nie wyglądające na polecenie powłoki, adres ani ścieżkę (`://`, `/…`, `~/…`, `./…`, `-flaga`, znaki powłoki `|`, `<`, `>`, `$`, `=` i odwrotny apostrof) — KRunner bywa też terminalem i paskiem adresu. Przechodzi przez `strip_secrets`; gdy coś w nim wygląda na sekret albo ścieżkę domową, nie idzie do modelu wcale. Po 0,3 s ciszy (KRunner pyta przy każdym naciśnięciu klawisza; nowsze zapytanie unieważnia starsze, zanim cokolwiek pójdzie do serwera), z pamięcią 64 ostatnich wektorów zapytań, z priorytetem `queue.HIGH` i limitem czekania w kolejce 2 s. KRunner czeka na wektor najwyżej 1,5 s — potem dostaje same słowa, a wywołanie kończy się w tle (zimny model ładuje się kilka sekund; następne zapytanie trafia już na załadowany, ok. 20 ms). Serwer nie odpowiada albo modelu nie ma — przez 30 s bez prób; zajęty serwer (raport) — bez przerwy. Żaden błąd nie trafia do KRunnera.
- **Ranking**: podobieństwo kosinusowe. Wynik musi mieć co najmniej 0,42, być o 0,06 ponad średnią wszystkich sesji (tło tego zapytania) i najwyżej 0,08 poniżej najlepszego; najwyżej 5 (w ramach limitu sesji: 8, przy `ai ` 20). Progi z pomiaru bge-m3 na przykładowych sesjach: niezwiązane teksty mają 0,3–0,4, pasujące 0,45–0,6.
- **Łączenie**: słowa mają pierwszeństwo — sesja znaleziona słowami nie powtarza się, a znaczeniowe mają kategorię 30 (LOW), trafność poniżej najsłabszej sesji ze słów (najwyżej 0,5) i dopisek w podpisie: `feat/login · 3 dni temu · podobne znaczeniowo`. Uruchomienie jak każdej sesji.
- **Wyłączenie**: `semanticSearch: false` w `models.json` (Tide → Modele, wiersz „Wyszukiwanie”, `SetSemanticSearch("off")`) — nic nie jest liczone, partia czekająca w kolejce albo trwająca jest przerywana, a plik indeksu znika od razu, w chwili zapisu ustawienia (także w usłudze z `--no-krunner`, która indeksu nie prowadzi, ale mogła go zostawić wcześniej). Stan indeksu: `Status().search` obiektu `/Models` (niżej).

**Dlaczego osobna nazwa i dbus-python.** KRunner oczekuje odpowiedzi `a(sssida{sv})` — tablicy struktur. PyQt6 nie potrafi jej zbudować: `QDBusArgument.beginArray` wymaga typu zarejestrowanego w C++ (`qDBusRegisterMetaType`), a próba obejścia przerywa proces w libdbus. Obiekt `/KRunner` obsługuje więc dbus-python (`python3-dbus`, z pętlą GLib z `python3-gi`), który ma pełne typowanie. Qt na Linuksie działa na tej samej domyślnej pętli GLib (`QEventDispatcherGlib`), więc obie biblioteki obsługuje jeden wątek główny; przy `QT_NO_GLIB` pętlę GLib popycha timer co 50 ms. dbus-python ma własne połączenie z magistralą, a nazwa należy do jednego połączenia — stąd `org.tide.Daemon.KRunner` obok `org.tide.Daemon`. Plik aktywacji D-Bus tej nazwy uruchamia tę samą jednostkę systemd, więc KRunner zapytany przed startem usługi ją uruchomi. Bez `python3-dbus` albo `python3-gi` usługa działa dalej, tylko bez wtyczki (`--no-krunner` wyłącza ją jawnie).

**Instalacja.** `make install-daemon` (kod i plik aktywacji `org.tide.Daemon.KRunner.service`) oraz `make install-krunner` (plik `.desktop`; oba są też w `make install`). KRunner czyta wtyczki D-Bus przy starcie, więc instalator i dezinstalator proszą działający KRunner o zamknięcie (`qdbus6 org.kde.krunner /MainApplication quit`) — wraca sam przy następnym Alt+Spacja. Wyszukiwanie w menu Plasmy (Kickoff) ma własną instancję i zobaczy zmianę po ponownym zalogowaniu albo `systemctl --user restart plasma-plasmashell`. `make uninstall-krunner` usuwa plik wtyczki, więc znika ona z KRunnera. Szybkie sprawdzenie: `qdbus6 org.kde.krunner /App querySingleRunner tide plasma` otwiera KRunnera z wynikami tylko tej wtyczki.

## Wydajność postoju (K-105, PT-458)

Kryterium gotowości „Gotowe (M0)”: usługa na postoju nie zajmuje więcej niż 30 % jednego rdzenia, `Snapshot` odpowiada
w p95 poniżej 1 s, a zatwierdzenie planu 18 wierszy trwa poniżej 10 s. Mierzy się to dwoma narzędziami o tej samej
mierze CPU: reproduktorem (przed instalacją, koordynator) i skryptem produkcji (po instalacji, właściciel).

### Narzędzie 10 okien CPU całego procesu

Kod: `daemon/tests/perf_cpu_windows.py` (miara i ocena), reproduktor `daemon/tests/perf_idle_cpu.py --scale prod
--windows 10`, skrypt produkcji `scripts/measure_idle_prod.py`.

- **Miara rozstrzygająca: CPU całego procesu usługi.** „Proces” to w reproduktorze PID usługi uruchomionej przez
  reproduktor, a na produkcji `MainPID` jednostki (`systemctl --user show -p MainPID`). Liczy się przyrost
  `utime + stime` z `/proc/<pid>/stat` — **wszystkie wątki procesu, bez procesów potomnych** (`cutime`/`cstime` nie są
  czytane). 10 kolejnych okien po 60 s, w % jednego rdzenia na okno; **próg: każde okno ≤ 30 %** (średnia nie
  ratuje okna ponad progiem). Wynik podaje PID, surowe przyrosty `utime` i `stime` (ticki) i % rdzenia każdego okna,
  średnią, maksimum, `box.enabled`, loadavg przed i po oraz SHA.
- **Czysty postój.** Rozgrzewka (`--warmup`, w reproduktorze i na produkcji domyślnie 60 s po starcie i sprawdzeniu
  ważności; nie ma jej jak wykryć bez żądań, więc to parametr) i 10 okien CPU **nie wysyłają żadnych żądań do usługi**:
  bez próbnika, `Snapshot` i innych wywołań D-Bus. Próbnik usługi (60 s: udział `engine.tick`, próbki `engine.tick`/s,
  najcięższe ramki) i seria 60 wywołań `Snapshot` co 0,5 s (p95 nearest-rank) idą w **osobnym oknie po** oknach CPU,
  bo próbnik sam zużywa CPU.
- **Ważność.** Zmiana PID (restart usługi) albo zniknięcie procesu unieważnia próbę; tak samo loadavg 1-min ≥ rdzenie/2
  przed pomiarem albo po oknach CPU (reguła PT-362), niepełna seria okien i przekroczenie limitu czasu. Na produkcji
  dodatkowo kroki w toku (jednostki `tide-step-*` i `tide-outside-*`) — jednym odczytem przed rozgrzewką, nie w trakcie.
- **Diagnostyka bez progu** (nie zmienia oceny): udział `engine.tick`, próbki `engine.tick`/s, CPU wątku workera przepływu
  (`flowThread` według reguły PT-410: jedyny wątek `tide-cardflow*`, inaczej największe `cpuS` bez wątku głównego
  i próbnika), `Snapshot` p95 (w reproduktorze; na produkcji próg `< 1 s` ocenia skrypt), oraz pamięć przed i po oknach CPU:
  `RssAnon`, `VmRSS`, `VmHWM` z `/proc/<pid>/status` i `MemoryCurrent` — w reproduktorze `memory.current` cgroup procesu
  (z zapisaną ścieżką cgroup i znacznikiem, czy cgroup zawiera inne procesy), na produkcji `MemoryCurrent` jednostki.
- **Ocena jednookienkowa PT-410** (`perf_idle_scale.py evaluate`, tryb PT-346 bez zmian) nie ma już progów ani werdyktu:
  CPU wątku przepływu, udział `engine.tick` i wzrost `VmRSS` są raportowane z oznaczeniem „tryb jednego okna — bez oceny
  progu; rozstrzyga tryb 10 okien”. Zostaje tylko ważność (loadavg).
- **Porównywalność.** Reproduktor nie ma m.in. `coordination_monitor._scan`, `describe.facts_hash`, `boardplans` ani
  `scanner` (~15 % próbek produkcji), więc jest wskaźnikiem. Rozstrzyga pomiar produkcji.

### Reproduktor: skrypt kolejki koordynatora

`daemon/tests/perf_k105_queue.py` — każde podpolecenie to **jedno polecenie `outside`**; dane syntetyczne, nic
z danych właściciela.

- `/usr/bin/python3 daemon/tests/perf_k105_queue.py build-data` — buduje dane 46 kart / ~60 MB (generator PT-410) **raz**
  do `build/k105-data/` w worktree (katalog ignorowany przez git) z manifestem `manifest.json` (parametry, skrót
  plików generatora, SHA repozytorium, rozmiar na dysku, czas budowy). Ponowne wywołanie przy zgodnym manifeście nic nie
  robi. Czas budowy zapisuje manifest (`buildSeconds`); budowa to osobne polecenie, żeby nie liczyć się w limicie pomiaru.
- `/usr/bin/python3 daemon/tests/perf_k105_queue.py measure <before|after> <false|true> [--base-sha SHA]` —
  **jedna konfiguracja (strona × `box.enabled`) i jedna seria 10 okien na polecenie**. Kopiuje dane z `build/` do świeżego
  katalogu tymczasowego (ścieżki w danych i klucz kopii dzienników są przepisywane na nowy katalog; `box.enabled`
  ustawia plik `cardflow.json`). Strona `before` uruchamia kod usługi z przypiętego SHA bazy karty (`--base-sha` albo
  `K105_BASE_SHA`) z własnego `git worktree add --detach` w katalogu tymczasowym i usuwa go po pomiarze
  (`git worktree remove` tylko tego katalogu, bez `prune`, bez zmiany gałęzi karty i ticketu); `after` — kod bieżącego
  worktree.
- **Wynik** konfiguracji: `build/k105-measure/<strona>-box-<false|true>.json` z listą prób; zapis przyrostowy po każdym oknie
  (próba `running` z oknami do tej pory).
- **Limit i wznawianie.** Próba ma wewnętrzny limit czasu (`--limit-min`, domyślnie 13 min: rozgrzewka 60 s + 10 okien
  + okno diagnostyki 60 s + start ≈ 12,5 min). Po przekroczeniu w rozgrzewce albo w oknach próba jest nieważna; limit
  przed oknem diagnostyki pomija tylko diagnostykę (okna CPU zostają ważne). Ponowne uruchomienie **tego samego polecenia**
  pomija konfigurację z ważnym wynikiem (także z oceną „niespełniony” — to ważny wynik) albo z wyczerpanymi 3 próbami
  i liczy próby dalej; próba zapisana jako `running` (polecenie ubite, np. kodem 143) liczy się jako nieważna.
  Jeden przebieg naraz (blokada pliku `build/k105-measure/.lock`), najwyżej 3 próby na konfigurację, rozstrzyga pierwsza ważna.
- Kody wyjścia `measure`: 0 — wynik ważny albo konfiguracja już rozstrzygnięta; 1 — 3 próby bez ważnej; 2 — ta próba
  nieważna (zgłoś to samo polecenie ponownie); 3 — inny przebieg trwa; 4 — brak danych albo zły SHA bazy.

Kolejność dla koordynatora: `build-data`, potem cztery polecenia `measure` (`before false`, `before true`, `after false`,
`after true`), każde osobno.

### Procedura produkcji po instalacji (właściciel)

Skrypt `scripts/measure_idle_prod.py` **tylko czyta**: niczego nie instaluje, nie zmienia konfiguracji (także
`box.enabled`), nie zapisuje w `~/.local/share`, `~/.config` ani w Tablicy — poza wynikiem (`--out`, odmawia ścieżek
w tych katalogach), plikiem żądania próbnika w katalogu uruchomieniowym i raportem próbnika zapisywanym przez usługę.

1. Po `make install` i restarcie usługi (nie w trakcie pracy agentów): `/usr/bin/python3 scripts/measure_idle_prod.py
   --out ~/pomiar-k105-box-<false|true>.json`. Skrypt sprawdza loadavg i **wymaga ciągłego postoju bez kroków w toku przez
   10 min** (`--wait-idle-min`, kryterium M0) przed rozgrzewką: odpytuje tylko odczyty kroków (bez żądań do usługi), a każdy
   krok zeruje licznik. Kroki szuka na hoście (`systemctl --user`) i — przy `box.enabled=true` — w uruchomionych
   pudełkach (`incus exec … systemctl --user`, jak usługa); błąd odczytu unieważnia pomiar, nie oznacza „bez kroków”.
   Czas postoju podany przez właściciela (`--owner-idle-min`, zapisywany w wyniku jako niezweryfikowany) zalicza się do
   wymaganych 10 min (skraca czekanie tylko o tyle, nie je zastępuje). Po `--max-wait-min` (domyślnie 30) bez osiągnięcia
   postoju wynik jest nieważny. Dalej rozgrzewka, 10 okien CPU `MainPID` na czystym postoju, potem osobno próbnik 60 s (przez plik żądania usługi) i seria
   `Snapshot`. Wynik JSON: okna, średnia, maksimum, pamięć przed i po, bieżące `box.enabled` odczytane z konfiguracji,
   ocena (próg CPU każdego okna ≤ 30 %, `Snapshot` p95 < 1 s). Kod wyjścia 0 — w progach, 1 — ponad próg, 2 — nieważny.
2. **Oba ustawienia `box.enabled`.** Właściciel przełącza je sam (zwykłym sposobem ustawień usługi, restartuje usługę
   i powtarza pomiar z kroku 1): raz `false`, raz `true`. Skrypt nigdy ich nie przełącza.
3. **Zatwierdzenie planu 18 wierszy (kryterium M0, < 10 s).** Osobny, jawnie nazwany tryb:
   `/usr/bin/python3 scripts/measure_idle_prod.py --mode approve-plan --card K-<numer> --out ~/pomiar-k105-zatwierdzenie.json`.
   To **jedyny tryb, który wywołuje akcję usługi** (`ApproveCardPlan`); uruchamia go wyłącznie właściciel dla karty z planem
   ≥ 18 wierszy, którą i tak zatwierdza. Mierzy czas od wywołania do zdarzenia `plan.approved` w dzienniku karty
   (`<Tablica>/flow/<karta>/journal.jsonl`, tylko odczyt) i zapisuje liczbę wierszy planu. Odmawia przy planie
   < 18 wierszy, chyba że właściciel poda `--allow-small-plan` — wtedy wynik ma znacznik „poza kryterium”.
4. **Gdzie zapisać wynik.** Pliki JSON zostają u właściciela poza repozytorium i poza Tablicą; do karty trafia podsumowanie
   (okna, maksimum, `Snapshot` p95, czas zatwierdzenia, `box.enabled`, SHA) wpisane przez koordynatora w notce domknięcia.
5. **Domknięcie karty kończy się pytaniem do właściciela o wynik pomiaru produkcji.** Wynik powyżej progu —
   CPU któregokolwiek okna w którymkolwiek ustawieniu `box.enabled`, `Snapshot` p95 ≥ 1 s albo zatwierdzenie planu ≥ 10 s —
   to **otwarty bloker M0**. Ten sam pomiar powtarza się po następnej instalacji (regresja).

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
Zatwierdzenie planu karty z lokalną numeracją (`ApproveCardPlan`, K-102) nadaje numery wszystkim wierszom `#n`
jedną rezerwacją zbiorczą (`reserve_many`): jedna blokada, jeden odczyt planów repozytorium, jeden zapis rejestru.
Wpisy mają kształt pojedynczej rezerwacji z kluczem (`<karta>:plan:<seq>:#n`), więc ponowienie i odzyskanie zamiaru
po restarcie zwracają te same numery. Więcej niż 50 nowych numerów w jednym wywołaniu to błąd bez zapisu (bez
dzielenia); dziennik karty nadal ma zamiar i wynik `reserve` dla każdego wiersza.

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
| `UpsertProject(s request)` | `→ s` | projekt `{slug, name, repo, tags, aiDocs, tool, entry, mode, description, youtrack, youtrackSync, reviewIndependence, reviewIndependenceClasses, assignmentRules, tests}` (`reviewIndependence`: `free|other-model|other-family`, inna wartość — błąd; opis w sekcji pliku projektu; `tests`: kontrakt testów, niżej „Kontrakt testów projektu”) albo oddzielny zapis wspólnych zasad `{globalAssignmentRules}` (`entry`: `planning` albo `spec`; sam `mode: full|light` bez `entry` znaczy Planowanie, a `mode: spec` — wejście `spec`, tak samo w UpdateCard, CreateCard, RenderLaunch i RenderFlow): istniejący (po `slug`, `repo` albo tej samej nazwie) — zmiana podanych pól, inaczej nowy → `{ok, slug, changed, commitError}`; z polem `tests` to także zatwierdzenie kontraktu (niżej „Zatwierdzenie kontraktu testów”) i odpowiedź ma dodatkowo `testsApproved` oraz `approvalError`; nadawca z jednostki kroku `tide-step-*` albo nieustalony → `{ok: false, error: "forbidden"}` bez zmian (niżej „Ochrona zatwierdzenia przed krokiem agenta”) |
| `SuggestProjectTests(s request)` | `→ s` | podpowiedź kontraktu testów `{slug}` albo `{repo}` → `{ok, changed, full, source, facts, message}`, dla `slug` także `effectiveSource` (`profile`/`detected`/`rules`), `unapproved`, `testsApproved`; tylko odczyt (niżej „Podpowiedź i źródło kontraktu”) |
| `UpsertTag(s request)` | `→ s` | tag `{name, color, description, newName}`; `newName` zmienia nazwę także w kartach i projektach (również tagu bez pliku — wtedy powstaje jego plik) → `{ok, name, changed, commitError}` |
| `RenderFlow(s id, s mode)` | `→ s` | polecenie dla karty (pusty `mode` — domyślne wejście i tryb projektu; `spec` to dawny zapis wejścia Tylko specyfikacja) → `{ok, text, entry, mode, tool, branch, check, docsDir, aiDocs, repo, repoMissing, project, projectName, specFile, template}`; katalog `docsDir` w tablicy zakłada od razu |
| `RenderLaunch(s id, s request)` | `→ s` | to samo z wyborem z okna „Co ma zrobić agent?”: `{entry, mode, tool, branch, check, preferredProvider}` (wszystko opcjonalne; `entry`: `planning` albo `spec` — Tylko specyfikacja, domyślne wejście projektu; `mode`: `full` albo `light`, dotyczy tylko Planowania i przy `spec` jest pomijany; `mode: spec` bez `entry` to stare żądanie wejścia Tylko specyfikacja; `check`: `auto` — domyślnie, albo `manual`); polecenie z każdego wejścia i trybu jest takie jak przed PT-77; odpowiedź ma `entry` i `mode` (pusty przy `spec`); odpowiedź ma też `projects` (dodatkowe projekty), `extraRepos` (ich istniejące repozytoria) i `addDirs` (katalogi dla `/Tasks Start`: dokumenty w tablicy przy `ai-docs: tide` i `extraRepos`) |
| `Badge()` | `→ s` | plakietka widoku Tablica w oknie Tide: JSON `{text, tone}` — „● N” (`waiting`), gdy karty czekają na ciebie, inaczej liczba kart w grze bez Gotowych i Archiwum (`muted`); zero — pusty tekst (plakietka ukryta). Tylko odczyt, niczego nie zakłada; ta sama reguła co `badge()` w `apps/board/backend.py` |
| `ShowWindow(s id)` | `→ b` | okno Tide na widoku Tablica (`tide.py --view board`), z ID karty (`K-12`, wielkość liter bez znaczenia) od razu na niej (`--select K-12`), z `launch:K-12` — na niej z oknem „Co ma zrobić agent?” (`--select launch:K-12`, „Uruchom” w popupie kafelka); napis, który nie jest ID karty, pomijamy (samo okno); `false`, gdy okna nie ma — kafelek tablicy w docku |
| `CardDocs(s id)` | `→ s` | plan i specyfikacja karty do panelu karty (niżej, „Plan i specyfikacja na karcie”): `{ok, card, plan, spec, others, warnings}`; tylko odczyt, odpowiedź po odczycie plików w wątku roboczym |
| `YouTrackIssues(b refresh)` | `→ s` | zgłoszenia użytkownika do importu (PT-7): `{ok, issues: [{id, summary, project, state, updated, url, card, boardProject, boardProjectName, new}], error}`; odpowiedź po pobraniu w wątku roboczym (`refresh: false` — ostatnia lista z pamięci); pokazane zgłoszenia liczą się jako przejrzane |
| `ImportYouTrack(s request)` | `→ s` | karty w Backlogu z listy `{issues: ["LOG-1", …]}` (najwyżej 200, tylko z ostatniej listy `YouTrackIssues`) → `{ok, created: [{issue, card, project}], skipped: [{issue, card, reason}], commitError}`; zgłoszenie z kartą — pominięte |
| `ResolveYouTrack(s id, s choice)` | `→ s` | konflikt ze zmianą ręczną w YouTracku: karta (`K-16`) albo klucz zgłoszenia, `choice`: `youtrack` (Tide przyjmuje stan z YouTracka) albo `tide` (zapis stanu Tide mimo zmiany) → `{ok, issue, choice}`; bez konfliktu — `not-found` |
| `ListCardBranches(s id, s request)` | `→ s` | bazy lokalne i znane lokalnie zdalne albo wyłącznie lokalne cele scalenia: `{purpose: "base"\|"merge", text?: "", limit?: 50}`; limit 1–100, filtr po całym zbiorze przed limitem; wynik `{ok, branches, mainRef, recentBases, recentTargets, warnings, hasMore}`; bez kursora, sieci i dowolnej ścieżki repozytorium od klienta |
| `SuggestCardBase(s id, s request)` | `→ s` | podpowiedzi bazy: `{text, revision: int, scope: "text"\|"files"\|"links"}`; wynik `{ok, revision, card, project, repo, candidates, warnings}`; tekst i historia plików lokalnie, linki PR/MR przez CLI; files/links wyłącznie na żądanie |
| `StartCard(s id, s request)` | `→ s` | zlecenie w przepływie karty: wejście „Od razu” `{entry: "now", executor, reviewer?, preferredProvider?, images?, baseRef?, baseCommit?, cardBranch?}` albo Planowanie `{entry: "planning", mode: "light"\|"full", executor, preferredProvider?, images?, check?, projects?}` (`projects` — projekty zlecenia, PT-115), albo propozycja planu `{entry: "plan", planPath}`; wynik zawiera `ok` i dane zlecenia albo `error`/`errors`; udany start karty z Backlogu przenosi ją do „Do zrobienia” (na koniec, jak `MoveCard`, w tej samej blokadzie co zapis zlecenia; odpowiedź ma wtedy `column: "todo"`) — także z `tide go` i skryptów koordynatora, a karta w innej kolumnie zostaje na miejscu; walidacja i przebieg opisane poniżej |
| `ResolveCardReview(s id, s choice)` | `→ s` | odpowiedź na pytanie po trzeciej lub kolejnej nieudanej rundzie review: `retry` uruchamia kolejne poprawki i review, `reject` odrzuca ticket bez scalenia, zachowując gałąź i worktree do wglądu; decyzja trafia do dziennika. `choice` to goły napis (`retry`, `reject`, `accept`) albo JSON `{"choice", "ticket", "round"?}`. Goły napis: przy jednym otwartym pytaniu o review działa jak dawniej; przy kilku zwraca `{ok: false, error: "ambiguous-review", reviews: [{ticket, round}, …]}` i niczego nie zapisuje. Z `ticket` rozstrzyga pytanie tego ticketu (najnowsza runda albo wskazana `round`); brak takiego pytania albo nieaktualna runda → `no-review-question`, bez zapisu. Błędny JSON, zły typ pól albo nieznany `choice` → `bad-choice`, bez zapisu. Odpowiedź sukcesu zawiera `ticket` i `round` rozstrzygniętego pytania. Okna Tablicy i Sesji wysyłają ticket i rundę pokazywanego pytania |
| `SetCardPaused(s id, b paused)` | `→ s` | wstrzymuje lub wznawia nowe kroki karty; trwające kończą się normalnie; stan w dzienniku jako `card.paused` / `card.resumed` |
| `EditCardPlan(s id, s request)` | `→ s` | zmiana nieruszonego ticketu (`action: edit`), propozycja jednego nowego ticketu (`action: append`), odesłanie z uwagami (`action: return`) albo wejście do planującego w Konsoli (`action: enter`) |
| `ApproveCardPlan(s id)` | `→ s` | zatwierdza poprawną propozycję planu karty: rezerwuje lokalne ID, zapisuje mapę `#n → ID` w dzienniku i uruchamia tickety po blokerach |
| `ApproveCardEnvironment(s id, s yaml, b onlyPlan)` | `→ s` | pakiet zgód środowiska karty (PT-172, niżej): usługa waliduje `yaml` (profil karty) i zgodę na pozycje czerwone przed jakimkolwiek zapisem; `onlyPlan` zatwierdza sam plan i ustawia `boxWait: environment` (PT-290: tylko przy `box.enabled: true`; przy wyłączonym pudełku zapis samej decyzji, bez budowy) |
| `BoxStatus(s slug)` | `→ s` | stan pudełka projektu, hash przepisu, „filtrowanie NIC: tak/nie” i `boxWait` kart projektu (PT-172, niżej) |
| `CardBranchDiff(s id)` / `CardBranchDiff(s id, s targetRef)` | `→ s` | po domknięciu diff względem bieżącego refu zapisanej bazy albo jawnego pełnego refu lokalnego/znanego zdalnego; unknown wymaga jawnego celu. Bez zapisu bazy i bez zgody na merge |
| `PreviewCardMerge(s id, s request)` | `→ s` | odczyt drzewa scalenia i lokalnych kolizji; `{}` wybiera lokalną bazę, jednoznaczny lokalny upstream bazy zdalnej albo główną przy bazie nieznanej; opcjonalny `targetRef` to pełny `refs/heads/…`, także nowy od znanej bazy zdalnej; zwraca `previewToken` i plan zasobów, bez zgody na wykonanie |
| `MergeCard(s id, s request)` | `→ s` | jawne potwierdzenie aktualnego podglądu (`targetRef`, `previewToken`); lokalny cel, w razie potrzeby ref/worktree tworzone po zgodzie; używa trwałej operacji PT-212 |
| `MergeCardToMain(s id)` | `→ s` | na kliknięcie użytkownika rozpoczyna trwałe scalenie domkniętej karty do gałęzi głównej i przenosi ją do Gotowych; odmawia przy lokalnych kolizjach, ponowienie wznawia ten sam zamiar |
| `Changed` | sygnał bez argumentów | migawka tablicy się zmieniła (zapis przez D-Bus, zmiana z zewnątrz, stan kart z pracy agentów) — pobierz `Snapshot()` ponownie |
| `WorkChanged` | sygnał bez argumentów | lista „Praca” się zmieniła — pobierz `Work()` ponownie. Od razu przy zmianie grup, pytań i liczb; gdy zmienia się tylko ostatnia czynność agenta (`note`), najwyżej co 10 s (`WORK_VOLATILE_MS`) |

`PreviewCardMerge` (PT-199) odpowiada asynchronicznie. Wynik zawiera `sourceRef`, `sourceCommit`,
`targetRef`, `targetCommit` (pełne refy i przypięte SHA), `sourceBranch`, `targetBranch`, `targetWorktree`
oraz `targetWorktreeExists`. Pełne listy `commits: [{sha, subject}]`,
`files: [{status, oldPath, newPath}]`, `conflicts: [ścieżka]` mają liczniki `commitCount`, `fileCount`,
`conflictCount`. Commity to dokładnie `targetCommit..sourceCommit`; pliki to różnica drzewa celu
i `resultTree` z `merge-tree`, z rozpoznawaniem rename i obiema ścieżkami. Zamknięcie widoku nie zapisuje
żadnej decyzji ani zmiany kolumny.

Konflikt jest poprawnym odczytem (`ok: true`, `canMerge: false`, `reason: conflict`), podobnie odmowy
`unrelated-history`, `git-capability`, `git-error`, `no-sandbox` mają własny `reason` i `message`.
Niepoprawne żądanie albo brak gałęzi daje standardowy błąd `ok: false`. PT-210 rozszerza `canMerge`
o domknięcie, czystość checkoutu karty, brak czynnych kroków (również Konsoli i poleceń poza piaskownicą),
niedokończonych akcji, zmian planu i korekty bazy po domknięciu. Cel jest wyłącznie lokalnym refem;
gałęzie kart i gałęzie/worktree czynnych kroków innych kart są wykluczone. Domyślny cel i jawny `targetRef`
korzystają z tej samej kwalifikacji. Ref zdalny, SHA ani skrót nazwy nie są celami tej metody.

`localChanges` i `localCollisions` zawierają osobne listy `staged`, `unstaged`, `untracked`.
Wpis zmiany to `{status, paths}` z surowym kodem porcelain i obiema ścieżkami rename; wpis kolizji
dodaje `mergePaths`. `localCollisionCount` liczy wpisy we wszystkich trzech listach kolizji.
Kolizja obejmuje usunięcia i relację plik–katalog w obu kierunkach, z granicą komponentu ścieżki.
`blockedReasons: [{reason, message}]` pokazuje wszystkie ograniczenia, a `reason`/`message` pierwsze z nich.
Konflikt drzew (`conflict`) i kolizja pracy lokalnej (`local-collision`) pozostają odrębne, również gdy
wystąpią równocześnie. Niezwiązane staged, unstaged i untracked nie blokują podglądu (K-43, PT-207).
Plik z jednoczesnymi staged i unstaged zachowuje oba poziomy pracy po scaleniu.
Cel bez checkoutu ma plan własnego worktree; metoda niczego nie zakłada. Zarejestrowany, niedostępny
checkout ma `no-target-worktree`. Błąd odczytu checkoutu blokuje
kwalifikację zamiast udawać czysty stan. Przy nieprzygotowanym drzewie `localCollisions` może pozostać `unassessed`.

`previewToken` jest HMAC od karty, zlecenia, `baseVersion` (także stabilnego 0 dla legacy/unknown),
przypiętych SHA i refów, odczytu worktrees, HEAD, surowego i logicznego indeksu, treści lokalnie zmienionych
plików oraz blokad przepływu. Brak checkoutu ma osobną reprezentację. Sekret żyje wyłącznie w silniku:
po restarcie nowy podgląd ma inny token. Token nie jest trwałą zgodą ani rozpoczętą operacją.
`MergeCard` ponownie przygotowuje podgląd i sprawdza aktualność tokenu pod blokadą repozytorium;
blokada obejmuje również przygotowanie i zapis zamiaru PT-212. Blokada dziennika chroni w tym czasie
stan zlecenia, domknięcia i wersję bazy przed innymi zapisami usługi.
Odczyt checkoutów i refów jest ponawiany po przygotowaniu drzewa; zmiana podczas odczytu daje
`state-changed`, `canMerge: false` i brak tokenu. Nieudany odczyt checkoutu również nie wystawia tokenu.
Historyczne pole `readyToMerge: false` pozostaje dla zgodności; samo otrzymanie podglądu nigdy nie jest zgodą.
Okno korzysta z `canMerge` i `previewToken`, aby włączyć osobny przycisk „Scal do <pełna nazwa>”.
Podgląd nie tworzy worktree i nie pisze indeksu, HEAD, refów ani plików;
dopuszcza obiekty próbnego drzewa. Sterowniki merge również działają w piaskownicy StepGit z zapisem
wyłącznie `objects`. Dotychczasowe jawne `MergeCardToMain` zachowuje sygnaturę i zachowanie.
Własny sterownik merge wymagający plików `.merge_file_*` w katalogu roboczym jest w podglądzie
odrzucany jako `git-error`: odczytowy mount blokuje już przygotowanie tych plików, przed uruchomieniem
sterownika. Nie zamieniamy tej odmowy narzędzia na konflikt ani nie otwieramy zapisu w worktree.

PT-204: `MergeCard` wymaga obiektu `{targetRef, previewToken}`. Błędny lub nieaktualny token (zmiana refów,
checkoutów, indeksu, lokalnej treści, zlecenia albo `baseVersion`) daje `stale-preview` bez rozpoczęcia operacji.
Po restarcie token nie pozwala rozpocząć nowej operacji. Aktualny podgląd z blokadą daje jej `reason`.
PT-200 dopuszcza jawnie wybrany lokalny cel przez tę samą operację. PT-206 dodaje przygotowanie
brakującego lokalnego refu i checkoutu po zgodzie, opisane poniżej.

Sukces zwraca `{ok, id, targetRef, targetBranch, sourceCommit, targetCommitBefore, resultCommit,
baseVersion, operationId, already}` z istniejącej operacji. `already` oznacza źródło już zawarte w celu.
Opcjonalne `operationId` wskazuje wyłącznie rozpoczętą operację tego samego zlecenia, refu i tokenu;
nie jest zgodą na rozpoczęcie nowej. Ponowienie pierwotnego żądania również odnajduje zapisane ID
po przypisanym tokenie, więc utrata odpowiedzi i restart nie powodują drugiego scalenia. Wznowienie
korzysta z kontroli recovery PT-212. Oba API kończą kartę zdarzeniem `card.merged` z rzeczywistym celem (PT-205);
stare `MergeCardToMain(id)` nadal wybiera główną i zwraca `main`, także gdy musi otworzyć własny checkout.

W Tablicy zarówno panel, jak i pełna karta otwierają podgląd po „Scal do <pełna nazwa>”. Potwierdzenie
pokazuje pełny ref i SHA źródła oraz celu. Zamknięcie, Escape, odświeżenie i `check: auto` nie wywołują
`MergeCard`. Po błędzie stary przycisk zostaje zablokowany; odświeżenie wymaga kolejnego potwierdzenia.

PT-200: przy znanej lokalnej bazie przycisk pokazuje „Scal do <pełna nazwa bazy>”. Menu „Scal…”
korzysta z `ListCardBranches(purpose=merge)`: wyłącznie pełne lokalne refy, bez duplikatów, z bazą
i nazwaną główną na początku pasujących wyników. Wyszukiwanie obejmuje cały zbiór przed limitem
50 (maks. 100); `hasMore` oznacza konieczność zawężenia. Lista współdzieli z podglądem kontrolę
gałęzi karty i chronionych refów/worktrees innych kart (`selectable: false`, `reason`).
Ocena kolizji treści pozostaje w podglądzie.
PT-214: `recentTargets` zwraca do 10 różnych istniejących lokalnych pełnych refów, od najnowszego
udanego `card.merged` lub starego `card.main-merged` ze znanym `main`. Nieznany cel nie jest zgadywany.
Lista jest odtwarzana z dzienników Tablicy po restarcie, wspólna dla worktrees rzeczywistego repozytorium
i odseparowana od innych repozytoriów; bez nowego magazynu. Usunięty ref znika ze skrótów, a historia
pozostaje. Skróty mają aktualny SHA oraz te same `selectable` i `reason` co wyniki wyszukiwania;
nie zależą od filtra i limitu wyników. Błąd historii daje ostrzeżenie i pozostawia ręczne wyszukiwanie.
Menu pokazuje bazę, nazwaną główną, ostatnie cele i wyszukiwarkę, bez powtarzania refów. Kliknięcie
skrótu otwiera jedynie podgląd, nie zmienia bazy ani domyślnego celu i nie daje zgody na scalenie.
Dla bazy nieznanej główna jest propozycją wymagającą wyboru. Wybór zmienia jedynie kontekst podglądu,
bez zapisu bazy ani zgody. Zmiana projektu lub
repozytorium karty zamyka wybór i podgląd oraz odrzuca ich spóźnione odpowiedzi.

PT-206: lista celów ma dodatkowo `defaultTargetRef`, `trackingTargets` i `createTarget` (obiekt albo null).
Dla zdalnej bazy tylko jedna lokalna gałąź z dokładnym pełnym upstreamem tej bazy jest domyślna;
zgodność SHA lub krótkiej nazwy nie wystarcza. Kilku kandydatów wymaga jawnego wyboru (`target-required`
przy podglądzie bez celu). Przy braku śledzącego celu menu proponuje „Utwórz lokalną <pełna nazwa>
od <remote/nazwa>”. Zajęta nazwa nigdy nie jest nadpisywana: menu pozwala wpisać inną albo jawnie wybrać
istniejący lokalny cel. Nowa nazwa trafia jako pełne `targetRef`; punkt startu pochodzi wyłącznie ze znanej
zdalnej bazy tego zlecenia, nigdy z dowolnego refu podanego przez klienta.

`targetPreparation` podglądu opisuje `createRef`, `createWorktree`, `path`, `startCommit`,
`remoteRef`, `remoteCommit`, dostępność nazwy i katalogu. Token obejmuje cały plan, nieistnienie refu
i checkoutu oraz SHA remote. Ruch remote, zajęcie nazwy/katalogu albo nowy checkout po podglądzie daje
`stale-preview` przed zapisaniem zamiaru i tworzeniem. Istniejący checkout ma pierwszeństwo; cudzy katalog
nie jest przełączany. Brakujący worktree ma nazwę `merge-target-<skrót>` niezależną od znaków refu.
Podgląd, lista i anulowanie nie tworzą zasobów; nie ma fetch, push, zmiany refs/remotes ani konfiguracji remote.

Pełny zamiar PT-212 powstaje przed zasobami. Dodatkowe checkpointy to `target-ref-created`,
`target-directory` (urządzenie/inode) i `target-worktree-created`. Atomowa transakcja refów tworzy cel
i dowód własności `refs/tide/merge-targets/<operationId>`; ten dowód pozostaje z zapisaną operacją.
Worktree ma podczas przygotowania blokadę opisaną ID operacji. Checkout początkowego drzewa działa
w StepGit; po zakończeniu gita własna blokada jest zwalniana. Recovery rozpoznaje zasoby także po awarii
przed checkpointem refu/worktree i kontynuuje ten sam wynik bez drugiego merge. Obcy ref (także zgodny SHA),
zmieniony inode, checkout albo commit dają oczekiwanie bez nadpisania. Awaria między `mkdir` a utrwaleniem
inode również bezpiecznie czeka. Zasoby nie są automatycznie usuwane przy porzuceniu zamiaru.
`card.merged` wskazuje rzeczywisty lokalny ref; projekcje i ostatnie cele używają tego wyniku.

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
| `max-tickets` | limit ticketów w toku na kartę (PT-375): liczba 1–6; brak klucza — ustawienie przepływu `ticketsPerCard`. Zapis: `UpdateCard {maxTickets}` (pusta wartość usuwa klucz), migawka: `maxTickets`. Zła wartość w pliku — limit domyślny i wpis w `errors` |
| `priority` | priorytet karty (PT-377): `high`, `normal` albo `low`; brak klucza — `normal`. Ustala kolejność kroków czekających na miejsce u dostawcy (sekcja „Kolejność kroków czekających na miejsce u dostawcy (PT-377)”). Zapis: `UpdateCard {priority}` i `CreateCard {priority}` (tylko te trzy wartości, inna → `{ok: false}` bez zmian; `normal` usuwa klucz z pliku), migawka: `priority`. Zła wartość w pliku — `normal` i wpis w `errors` („niepoprawny priorytet”), bez błędu odczytu karty. Okno: „Priorytet” w ustawieniach pełnej karty (Wysoki / Normalny / Niski) |
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
- `review-independence` (JSON `reviewIndependence`, K-88/PT-330, decyzja 96 z 10.11): polityka niezależności review projektu —
  `free` (domyślnie, także bez klucza w pliku; zapis `free` nie dopisuje klucza), `other-model` (inny model albo inna
  siła myślenia niż autor) albo `other-family` (inna rodzina: para dostawca/program z katalogu wykonawców, nie nazwa).
  `review-independence-classes` (JSON `reviewIndependenceClasses`, lista klas `lekki`, `standard`, `mocny`, `lokalny`,
  w pliku po przecinku) zawęża `other-family` do ticketów tych klas; pozostałe dostają `free`, a review planu
  stosuje politykę bez zawężenia. `UpsertProject` odrzuca nieznaną wartość (`bad-request`); nieznaną wartość wpisaną
  ręcznie do pliku migawka pokazuje bez zmian, a usługa stosuje `free` z ostrzeżeniem w dzienniku usługi
  (`cardflow.reviewgate`, raz na wartość i plik). Usługa czyta ustawienie z bieżącej Tablicy przy każdym sprawdzeniu,
  więc zmiana działa bez restartu. Okno Tablicy ustawia je w formularzu projektu („Recenzent”: Dowolny, Inny model,
  Inna rodzina, z polem klas). Niezależnie od polityki recenzent zawsze startuje w nowej, osobnej sesji.
- Projekty powstają jawnie (`UpsertProject`; ta sama nazwa drugi raz to ten sam projekt, nie `foo-2`) albo przy pierwszej karcie dla repozytorium (`CreateCard` z `repo`) — osobnym commitem. Usługa nie zakłada wpisów dla wszystkich repozytoriów z katalogu projektów; migawka podaje je w `repos` jako kandydatów.

### Kontrakt testów projektu (K-69, PT-278)

Projekt Tablicy ma kontrakt testów: polecenie „testy dobrane do zmian” (`tests.changed`, z parametrem `{base}`)
i „pełne testy przy domknięciu” (`tests.full`). Leży w profilu projektu `projects/<slug>/box.yaml` jako klucz
`tests` schematu `tide-box: 1` — w tym samym pliku co profil pudełka K-38, **nie** we frontmatterze
`projects/<slug>.md`. Zatwierdzenie przez użytkownika opisuje „Zatwierdzenie kontraktu testów” niżej (PT-285),
a ochronę przed krokiem agenta — „Ochrona zatwierdzenia przed krokiem agenta” (PT-286).

```yaml
tide-box: 1
tests:
  changed: "make test-changed BASE={base}"
  full: "make test"
```

- Kanoniczny zapis: napisy w podwójnym cudzysłowie kodowane przez `json.dumps`, wcięcie dwiema spacjami.
  Odczyt rozpoznaje cały token napisu dekoderem JSON; dopiero po jego końcu dopuszcza odstępy i komentarz
  `#`. Apostrofy YAML i napisy niecytowane pozostają obsługiwane przy ręcznej edycji profilu. Brzegowe
  odstępy wejścia normalizuje `validate_tests`; odstępy wewnątrz polecenia pozostają bez zmian.
- `UpsertProject` przyjmuje `tests: {"changed": "…", "full": "…"}`. Pusty napis czyści pole, brak klucza go nie
  zmienia, brak `tests` w żądaniu nie dotyka `box.yaml`. Gdy oba pola są puste, klucz `tests` znika z pliku
  (plik zostaje, np. z samym `tide-box: 1`). Bez pliku zapis zakłada go z `tide-box: 1`.
- Zapis podmienia albo dodaje (na końcu pliku) wyłącznie blok `tests` na wcięciu 0 wraz z jego wciętymi liniami;
  reszta pliku (klucze K-38, komentarze, wcięty `tests` pod innym kluczem) zostaje bajt w bajt. Plik `box.yaml`
  trafia do tego samego commita Tablicy co zmiana `projects/<slug>.md`; sam zapis `tests` nie zmienia pliku `.md`.
- Reguły (`validate_tests`): jedna linia, bez NUL, najwyżej 300 znaków; `changed` musi zawierać `{base}` i nie
  przyjmuje innych parametrów w klamrach; `full` nie przyjmuje żadnych. Odrzucenie to błąd `bad-request` bez zmiany
  plików i commita. Polecenie z `*` albo niebędące pojedynczym poleceniem (`single_command`) jest poprawne, ale
  `grant_note(polecenie)` zwraca komunikat, że Tide nie nada mu automatycznej zgody.
- JSON projektu (migawka, lista projektów): `tests` `{changed, full}` (zawsze oba klucze), `testsError` (napis,
  pusty bez błędu), `testsApproved` (niżej) i `testsGrantNote` `{changed, full}` z `grant_note`. Uszkodzony blok (zły typ, zły YAML, nieznany
  klucz, naruszenie reguł) daje pusty kontrakt, `testsError` i wpis w błędach Tablicy; reszta listy działa dalej.
  Zapis `tests` naprawia taki blok.
- Moduł: `daemon/tide_daemon/board/project_tests.py` (`validate_tests`, `load_tests`, `save_tests`, `grant_note`),
  poza katalogiem `box/` K-38.

#### Zatwierdzenie kontraktu testów (PT-285)

Kontrakt leży w plikach Tablicy, więc może go zmienić krok agenta, ręczna edycja albo cofnięcie commita.
Zatwierdzenie to **odcisk** kontraktu zapisany poza Tablicą, w stanie usługi:
`$XDG_STATE_HOME/tide/test-contracts.json` (domyślnie `~/.local/state/tide/`).

```json
{"boards": {"/home/…/tide-board": {"mobileapp": "<sha256 hex>"}}, "version": 1}
```

- **Odcisk** = SHA-256 reprezentacji kanonicznej `{"changed":"…","full":"…"}` (stała kolejność pól, JSON bez
  odstępów, UTF-8). Brak pola ≡ pusty napis; usuwane są wyłącznie białe znaki na brzegach każdego polecenia —
  odstępy wewnątrz (także w cytowanym argumencie) są znaczące. Klucz: ścieżka Tablicy po `realpath` i slug.
- **Zasada zatwierdzenia.** Odcisk tworzy wyłącznie `UpsertProject` z polem `tests` (okno projektu):
  `board_monitor` woła `BoardStore.approve_project_tests`, które zapisuje kontrakt jak `upsert_project`,
  sprawdza pod tą samą blokadą Tablicy, że plik ma dokładnie ten kontrakt, i zapisuje odcisk całego kontraktu
  po zapisie (zapis częściowy, np. samo `full`, zatwierdza oba pola). Pusty kontrakt usuwa odcisk. Samo
  `BoardStore.upsert_project` (np. dopisanie dozwolonych poleceń przez silnik kart), odczyt listy, migawka
  i polecenia dla karty (`RenderFlow`, `RenderLaunch`) odcisku nie tworzą ani nie zmieniają. Ponowne
  zatwierdzenie identycznych wartości (po utracie odcisku albo przywróceniu treści) zapisuje odcisk bez zmiany
  plików Tablicy i bez commita (`changed: false`, `testsApproved: true`).
- **`testsApproved`** w JSON projektu: `true` tylko wtedy, gdy kontrakt jest niepusty, blok poprawny, a jego odcisk
  równy zapisanemu. Kontrakt różny od odcisku (albo bez odcisku) ma `false` i wpis w błędach Tablicy
  „`projects/<slug>/box.yaml`: kontrakt testów zmieniony poza oknem projektu — sprawdź i zatwierdź”; zapis
  w oknie projektu go zatwierdza. Pusty kontrakt i uszkodzony blok mają `false` bez tego wpisu (blok ma własny błąd).
  Odpowiedź `UpsertProject` z `tests` ma `testsApproved` i `approvalError` (pusty bez błędu); błąd zapisu
  odcisku nie cofa zapisanego kontraktu — zostaje niezatwierdzony.
- **Plik odcisków.** Katalog `tide` 0700, plik 0600, właściciel = użytkownik usługi. Odczyt otwiera katalog
  i plik z `O_NOFOLLOW` (plik także z `O_NONBLOCK`), sprawdza `fstat` i limit 1 MiB. Brak pliku albo katalogu,
  dowiązanie, zły właściciel, złe prawa, nie zwykły plik, uszkodzony JSON albo zły kształt dają `false`,
  nigdy `true` i nigdy błąd listy projektów. Zapis: plik tymczasowy w tym samym katalogu (`O_EXCL|O_NOFOLLOW`,
  0600), `fsync`, `rename` względem uchwytu katalogu, `fsync` katalogu; przerwanie w połowie zostawia stary
  plik i usuwa tymczasowy. Zapisy szereguje `flock` na uchwycie katalogu. Zapis poprawia prawa katalogu `tide`
  na 0700, a nieczytelny plik zastępuje nowym.
- Moduł: `daemon/tide_daemon/board/contract_approval.py` — jedno wejście odczytu `approved(board, slug, contract)`
  i jedno zapisu `record(board, slug, contract)`; z nich korzysta PT-286.
- **Granica.** Krok korzysta z zatwierdzenia wyłącznie przez `approval_for_step` (niżej, PT-286); integracja
  z silnikiem kart to PT-287. Do tego czasu `testsApproved` jest tylko informacją w oknie.

#### Ochrona zatwierdzenia przed krokiem agenta (PT-286)

Prawa 0600 nie odróżniają usługi od kroku agenta tego samego użytkownika, a martwy `DBUS_SESSION_BUS_ADDRESS`
ze środowiska kroku agent może nadpisać. Dlatego zatwierdzenie chronią dwa warunki i próby rzeczywistej piaskownicy.

- **Kolizja ścieżek — `approval_for_step(project, step, board)` → `(approved, reason)`**
  (`contract_approval.py`; w usłudze `BoardStore.approval_for_step(project, step)`). Zanim kontrakt zostanie
  dopuszczony dla kroku, funkcja porównuje plik odcisków i każdy jego katalog nadrzędny — w postaci podanej
  i po `realpath` (z rozwinięciem dowiązań) — z **każdym obszarem zapisu kroku**, także po `realpath`. Ścieżki
  magazynu nie normalizuje się leksykalnie przed rozwinięciem dowiązań: `link/..` (także w `XDG_STATE_HOME`) to
  katalog nadrzędny celu dowiązania, jak w systemie plików, więc sprawdza się ten sam plik, który potem jest
  czytany. Ścieżka jest rozwijana składnik po składniku jak w jądrze: sprawdza się każdy wpis katalogu na drodze
  rozwinięcia (rzeczywisty katalog + nazwa), także wpisy z treści napotkanych dowiązań — dowiązanie pośrednie
  w obszarze zapisu (`/safe/alias → /worktree/pivot → /safe/target`) jest kolizją, bo krok mógłby je przestawić
  między kontrolą a odczytem — oraz cel każdego przedrostka. Pętla dowiązań albo błąd ich odczytu to
  niepewność, czyli kolizja. Kolizja:
  plik albo katalog nadrzędny leży w obszarze zapisu, albo obszar zapisu leży w katalogu odcisków. Wtedy
  `approved: false`, powód „magazyn zatwierdzeń leży w obszarze zapisu kroku: …” i ten sam wpis w błędach Tablicy
  (z prefiksem slugu), dopóki kolejne sprawdzenie tego projektu nie przejdzie bez kolizji. Bez kolizji wynik daje
  odczyt magazynu (`approved`). Każda niepewność — błędne ustawienia kroku (`StepError`), worktree nieznany
  repozytorium, pusta lub względna ścieżka obszaru, obszar `/`, dowolny wyjątek — to `approved: false`.
- **Obszary zapisu** daje jedna funkcja `tasks/agentcmd.py`: `write_areas(step, env, git_dir, skills)` →
  `WriteAreas`; z niej `_claude` składa `allowWrite`, `--add-dir` i `denyWrite`, a `_codex` — `--cd`,
  `writable_roots` i wykluczenia `/tmp`. `step_write_areas(step)` ustala jej wejścia tak samo jak `background()`
  (bez zakładania plików), więc sprawdzenie i argumenty kroku nie mają dwóch kopii. `WriteAreas.writable()`:
  - Claude: katalog roboczy sesji (worktree albo `out` kroku opiekuna), `allowWrite` (`out`), każdy `--add-dir`
    nieobjęty `denyWrite` (dziś `out`; katalogi tylko do odczytu, wyniki poprzednich kroków i skille mają
    `denyWrite`), `CLAUDE_CODE_TMPDIR` oraz zapis dawany przez program sam: `/tmp/claude`, `/private/tmp/claude`,
    `/tmp/claude-<uid>` i wspólny `.git` repozytorium,
  - Codex: `--cd`, `writable_roots` (katalog worktree w `.git/worktrees`, `.git/objects|refs|logs`, `out`) oraz
    `/tmp` i `$TMPDIR` programu — tylko gdy krok ich nie wyklucza (`exclude_slash_tmp`, `exclude_tmpdir_env_var`:
    krok opiekuna, katalogi tylko do odczytu, profil odczytu); kolizja liczy się z faktycznych ustawień kroku,
  - zawsze repozytorium Tablicy (dokumenty AI i Konsola piszą w nim; dokłada je `approval_for_step`).
- **Pochodzenie wywołania.** `UpsertProject` z polem `tests` ustala PID nadawcy z magistrali
  (`GetConnectionUnixProcessID`, `QDBusConnectionInterface.servicePid`) i czyta `/proc/<pid>/cgroup`
  (`daemon/tide_daemon/call_origin.py`). Proces w jednostce kroku (segment `tide-step-*`, także podgrupa jednostki
  i wpisy hybrydowe cgroup v1), brak PID, PID 0, brak procesu, pusty, nieczytelny albo niezrozumiały plik →
  odrzucenie `{ok: false, error: "forbidden", message}` bez zmiany `box.yaml`, odcisku i commita (także przy
  ponownym zatwierdzeniu identycznych wartości). Bez pola `tests` `UpsertProject` działa jak dotąd i nie sprawdza
  nadawcy. Zapis kontraktu w pliku przez krok nie zmienia `testsApproved` (PT-285). Polecenia poza piaskownicą
  (`tide-outside-*`) i okno projektu to nadawcy spoza kroku. Opcja usługi `--proc-root` (ukryta) służy wyłącznie
  testom (atrapa `/proc`). Ograniczenie: proces przeniesiony poza jednostkę kroku (np. `systemd-run --user`
  z wnętrza kroku) nie byłby rozpoznany — tę drogę musi zamykać piaskownica, co sprawdzają próby niżej.
- **Próby rzeczywistej piaskownicy (warunek).** Ochrona obowiązuje tylko wtedy, gdy w prawdziwych piaskownicach
  Claude i Codexa (`workspace-write`) z ustawieniami z `tasks/agentcmd.py` każda z prób kończy się odmową
  piaskownicy albo odrzuceniem przez sprawdzenie pochodzenia, a odcisk i `box.yaml` zostają bez zmian:
  (a) zapis, podmiana (`rename`), usunięcie pliku odcisków, przeniesienie jego katalogu, dowiązanie w katalogu
  stanu, zapis w prawdziwym katalogu magazynu, zapis `box.yaml`; (b) `UpsertProject` z `tests` przy martwym
  adresie magistrali, przy podanym prawdziwym adresie magistrali usługi i przy nadpisanym
  `DBUS_SESSION_BUS_ADDRESS`; (c) proces poza jednostką kroku przez `systemd-run --user` (także z
  `XDG_RUNTIME_DIR` i prawdziwą magistralą) i `UpsertProject` z takiego procesu. Udana próba oznacza, że
  zatwierdzenia nie wolno dopuścić do kroków. Skrypt: `scripts/sandbox/approval_trials.py <katalog wyniku>`
  (poza piaskownicą; usługa z kodu repozytorium na osobnej magistrali, magazyn próbny w
  `~/.local/state/tide-pt286-trials`, raport `report.md`), próby w kroku: `scripts/sandbox/approval_probe.py`.
  Każda zmiana `tasks/agentcmd.py` dotycząca zapisu, sieci albo środowiska kroku wymaga ponownego przebiegu.
  Skrypt wybiera program i pomijane serwery MCP Codexa tak jak usługa: Codex z aplikacji ChatGPT zamiast snapa,
  bo snap nie działa w jednostce `tide-step-*`, oraz `codexMcpSkip` z ponowieniem.
  **Wynik dla PT-286 (8.11.2026, HEAD `b2b79238`):** w Claude i w Codexie (`workspace-write`) wszystkie próby
  skończyły się odmową. Claude: katalog domowy jest niewidoczny w piaskownicy, a tworzenia gniazd AF_UNIX zabrania
  seccomp, więc padają magistrala usługi, magistrala pulpitu i `systemd-run --user`. Codex: system plików poza
  obszarami zapisu jest tylko do odczytu (`Read-only file system`), a połączenie z każdym gniazdem magistrali kończy
  się „Operation not permitted”, więc `systemd-run --user` też nie działa. Odcisk, `box.yaml` i HEAD Tablicy zostały
  bez zmian. Sprawdzenie pochodzenia nie miało czego odrzucać, bo żaden krok nie połączył się z magistralą; chroni
  ono na wypadek przyszłego poluzowania piaskownicy. Szczegóły każdej próby są w notce ticketu.

#### Podpowiedź i źródło kontraktu (PT-282)

`SuggestProjectTests(s json) -> s` — tylko odczyt; wejście `{slug}` albo `{repo}`, odpowiedź
`{ok, changed, full, source, facts, message}`, dla `slug` dodatkowo `effectiveSource`, `unapproved`, `testsApproved`.

- **Podpowiedź** liczy `suggest_tests(facts)` (`cardflow/testpolicy.py`) na faktach `repo_facts`, które bierze ze
  wspólnej `make_targets` (ta sama reguła i te same zabezpieczenia co w kartkach: nic nie uruchamia, dowiązania
  poza repozytorium pomija). Cel `test-changed` → `make test-changed BASE={base}`, cel `test` → `make test`
  (źródło `make`); cele bada się niezależnie, więc samo `test` daje `full` bez `changed`
  (`facts.legacy: true`). Bez celów albo bez `Makefile` wynik jest pusty, `source` = `""`, a `message` mówi „brak
  podpowiedzi — kartki odeślą do zasad projektu”. Fakty to na razie wyłącznie cele `make`; przełączenie na
  `StackReport` K-38 czeka na warunek ze szkicu k38-tests-schema.
- **Źródło** (`effectiveSource`: `profile`, `detected`, `rules`) i `unapproved` to ta sama decyzja `effective_tests`
  co w kartkach, policzona na repozytorium projektu z kontraktem z `box.yaml` i `testsApproved` z magazynu odcisków.
  Okno nie jest krokiem, więc kolizja ścieżek z ochroną zatwierdzenia go nie dotyczy.
- **`repo`** przechodzi tę samą walidację co `repo` projektu (`git_repo_path` w `board/store.py`, wspólna z `_repo`):
  ścieżka bezwzględna istniejącego katalogu Git, także worktree (plik `.git`); inaczej `bad-request` z komunikatem.
  Nieznany `slug` → `not-found`. Projekt z brakującym repozytorium (`repoMissing`) albo repozytorium bez celów to
  pusty wynik z komunikatem, bez błędu.
- Podpowiedź niczego nie zapisuje i nie zatwierdza; kontrakt powstaje dopiero przy zapisie projektu z okna.

#### Zgodność z profilem pudełka K-38

Wzorcowy plik profilu z kluczami K-38 (`apt`, `network`, `verify`) i blokiem `tests`:
`daemon/tests/fixtures/box-profile/box.yaml`. Test `daemon/tests/test_board_project_tests.py` czyta go przez
`load_tests` i porównuje z wynikiem `save_tests`. Szkic zmian po stronie K-38:
[k38-tests-schema](agents/plans/followups/k38-tests-schema.md). Warunki scalenia drugiej z kart K-38/K-69
do głównej (spełnione warunki odhaczone przez PT-321; testy: `daemon/tests/test_box_profile_tests_key.py`):

- [x] `tests` w zamkniętej liście kluczy profilu projektu, w `KEY_ORDER` i w `NON_HASH_KEYS` (jako `tests.changed`,
  `tests.full`): zmiana samych poleceń testów nie zmienia hasha przepisu. Profil karty odrzuca `tests`
  (`validate(..., "card")`), a `merge(project, card)` przenosi kontrakt z projektu bez zmian;
- [x] walidacja `tests` deleguje do `validate_tests` (jedna reguła, bez zmian po stronie K-69);
- [x] czytnik `yamlmini` rozpoznaje kanoniczne napisy JSON także z końcowymi ukośnikami i komentarzem poza
  napisem: koniec napisu w cudzysłowie wyznacza pełne przejście ucieczek (ukośnik zawsze ucieka następny znak),
  a wartość i jej koniec — dekoder JSON (`raw_decode`); w apostrofach `''` jest apostrofem w napisie. Regresje
  generowanych poleceń z `test_board_project_tests.py` czyta też `bp.load_project`, także po zapisie K-38
  i częściowej aktualizacji przez `BoardStore`;
- [ ] cele `make` (`test-changed`, `test`, `lint`) dostępne w raporcie niezależnie od rozpoznania Pythona
  (poza PT-321: `followups/stack-hints.md`);
- [x] wzorcowy plik czytany bez błędu przez `box/profile.py` (`load_project`; wynik zawiera `tests`);
- zmienione ścieżki uruchamiania kroków (start, wznowienie, konflikt, domknięcie, kroki pudełka) sprawdzone pod kątem
  wywołań polityki testów (po #10, #7 i #3 planu K-69): każda z nich nadal liczy jeden `TestPolicy` dla kartki
  i zgód (`step_test_policy`), czego dowodzą testy przepływu z atrapą procesu, nie sama zgodność `box.yaml`. Zasada
  ogólna, na której opiera się ten warunek: [testing.md](testing.md#zasada-ogólna-i-kontrakt-testów-projektu).
  PT-321 nie zmienia ścieżek uruchamiania kroków (tylko odczyt i zapis profilu).

Wspólny plik `projects/<slug>/box.yaml` (PT-321):

- **Odczyt.** `bp.read_project(slug)` zwraca `ProjectFile`: blok `tests` czyta reguła K-69 (`load_tests`), resztę
  pliku `yamlmini` i schemat K-38. Uszkodzony blok `tests` nie unieważnia profilu K-38: profil jest bez `tests`, a
  błąd (`tests_error`, ten sam napis co `testsError` w `BoardStore`, z komunikatem `validate_tests`) trafia do uwag
  sekcji „Środowisko karty”. `bp.load_project` zwraca profil z poprawnym, niepustym kontraktem w `tests`.
- **Profil bez ustawień K-38 jest nowy.** Plik z samymi `tide-box`, `project` i `tests` (np. założony przez
  `save_tests`) nie jest zatwierdzonym profilem K-38: `load_project` zwraca None, a zgoda środowiska pokazuje
  propozycję i pakiet zgód jak projekt bez profilu. Zatwierdzenie pustego profilu zapisuje `box: per-project`
  (wartość domyślna, poza hashem), żeby kolejny odczyt nie uznał go znów za nowy.
- **Zapis K-38.** `bp.save_project` (jedyna ścieżka zapisu profilu projektu: `envconsent` przy `box.enabled` true i
  false, `tide box approve … --accept-red`) pomija `tests` z wejścia i przenosi blok `tests` z pliku bajt w bajt,
  także uszkodzony. Nie zapisuje `testsApproved` ani nie dotyka magazynu odcisków (PT-285/PT-286): zatwierdzenie
  kontraktu liczy wyłącznie `BoardStore`. Istniejącego pliku, którego nie da się przeczytać (YAML lub schemat poza
  blokiem `tests`, inny `project`, `tests` poza blokiem najwyższego poziomu, rozmiar, kodowanie), zapis nie
  nadpisuje — `ProfileError`, a zgoda środowiska odpowiada błędem bez zapisu.
- **Wspólna blokada.** `save_project` czyta i zapisuje plik pod tą samą blokadą co `BoardStore.transaction`
  (`board/lockfile.py`; wielowejściowa w obrębie wątku): `flock` na deskryptorze katalogu Tablicy — działa także na
  poprawnej Tablicy bez `.git` (np. kopii plików), którą `BoardStore.ensure()` dopiero inicjalizuje — a gdy
  repozytorium istnieje, dodatkowo `flock` na `<tablica>/.git/tide-board.lock` (zgodność z K-55). Kolejność stała:
  katalog, potem `.git`. Przeplot zapisu K-38 i zapisu kontraktu w dowolnej kolejności, także przed inicjalizacją
  gita, daje najnowszy kontrakt i najnowsze ustawienia K-38. Bez katalogu Tablicy sam odczyt niczego nie zakłada
  i nie bierze blokady; zapis, który zakłada Tablicę (`ensure()`), zakłada katalog, bierze blokadę przed
  inicjalizacją i trzyma ją do końca zewnętrznej transakcji — świeży odczyt, plan i zapis kontraktu po założeniu
  Tablicy nie przeplatają się z zapisem K-38 (review PT-321 r2).
- **Znane ograniczenie: komentarze.** Zapis K-38 (`yamlmini.dumps`) przepisuje plik poza blokiem `tests`, więc
  komentarze poza tym blokiem (np. nad `tests:`) znikają; komentarze wewnątrz bloku zostają. Zapis kontraktu
  (`save_tests`) nadal zachowuje cały plik, w tym komentarze — obietnica historii 10 specyfikacji dotyczy zapisu
  kontraktu, nie zapisu profilu K-38.

#### Efektywny kontrakt testów w kartkach kroków (PT-279)

Kartki ticketu, poprawek, review i domknięcia (`cardflow/handoff.py`) biorą polecenia testów z `TestPolicy`
(`cardflow/testpolicy.py`). Decyzja `effective_tests(contract, worktree, approved, base, error=…)`:

1. zatwierdzony kontrakt (`approved` — wartość logiczna z zewnątrz; decyzja nie liczy odcisku) → źródło `profile`;
   brakujące pole uzupełnia zasada ogólna, nie wykrycie;
2. bez zastosowanego kontraktu (brak, niezatwierdzony, uszkodzony profil `testsError`) — wykrycie
   `make_targets(worktree)`: cel `test-changed` → `make test-changed BASE={base}`, cel `test` → `make test`
   (cele badane niezależnie) → źródło `detected`;
3. reszta → zasada ogólna (`rules`): „testy dobrane do zmian według zasad projektu (CLAUDE.md/AGENTS.md)”, przy
   domknięciu — pełne testy według zasad projektu.

`legacy` — gałąź z `test` bez `test-changed`: pełny `make test` w tickecie, poprawkach i review, a recenzent
sprawdza w notce udany pełny `make test` (jedyny wyjątek od zakazu żądania pełnego przebiegu). Zatwierdzone
`make test-changed …` na gałęzi z samym `test` też daje `legacy`; na gałęzi bez obu celów `changed` przechodzi na
zasadę ogólną z linią o brakującym celu (tak samo zatwierdzone `make test` bez celu `test` przy domknięciu).
Sprawdzenie celu dotyczy tylko zwykłego `make <cel> [ZMIENNA=…]`: polecenie z opcją `make` (`-C tests`,
`-f tests/Makefile`, `-j4`) zostaje zatwierdzonym poleceniem bez sprawdzania, bo cele głównego `Makefile`
i `mk/*.mk` nie rozstrzygają o nim. Linia źródła zasady ogólnej przy zastosowanym kontrakcie podaje rzeczywistą
przyczynę: kontrakt nie podaje tego polecenia albo gałąź nie ma celu z kontraktu.
`{base}` zastępuje przypięty punkt startu ticketu (`shlex.quote`); bez bazy zostaje nierozwinięte
(`base_pending`), a kartka opisuje je jako przypięty punkt startu ticketu. Kartka ma linię źródła polecenia
(„wykryte z Makefile”, „zatwierdzony kontrakt…”, „zasady projektu…”) i linię o niezatwierdzonym kontrakcie,
który nie jest stosowany. Przepis ponowienia przez `tools/test_runner.py` dostają tylko polecenia `make test`
i `make test-changed`; inne (np. `dotnet test …`) — ogólną zasadę ponowienia.

`make_targets(katalog)` to jedyne miejsce przepływu karty czytające `Makefile` i `mk/*.mk`: tylko odczyt, bez
uruchamiania programów, z pominięciem plików i katalogu `mk` wychodzących dowiązaniem poza katalog. Do czasu
integracji z silnikiem (#10) silnik woła kartki bez polityki, a kartki liczą ją `master_policy(worktree, base)`
— regułą sprzed K-69 (bez `Makefile` nadal `make test-changed` i `make test`, bez linii źródła). Wzorcowe
fragmenty kartek z master: `MASTER_FRAGMENTS` w `daemon/tests/test_cardflow_effective_tests.py`.

**Kartka konfliktu i linia planowania (PT-284).** Krok rozwiązania konfliktu scalenia (`conflict`) liczy
politykę przez `step_test_policy` przy starcie i każdym wznowieniu, jak ticket, poprawki, review i domknięcie
(zdarzenie kroku ma `testPolicy` i `testPolicyCard`, wznowienie dostaje sekcję „Obowiązujące testy” w wariancie
konfliktu). Bazą jest przypięty SHA z wyniku utworzenia gałęzi scalanego ticketu (`ticket_of(st, ticket)["base"]`),
nie wierzchołek gałęzi karty; bez znanej bazy `{base}` zostaje nierozwinięte i opisane. Kartka konfliktu
(`conflict_tests`) każe po zapisaniu commita scalenia uruchomić w worktree karty: `tests.changed` zatwierdzonego
kontraktu, wykryte `make test-changed BASE=…`, w `legacy` pełny `make test`, a bez polecenia — testy dobrane do
zmian według zasad projektu; w każdym wariancie polecenie i wynik idą do notki, a `done` wymaga udanego przebiegu.
Dawne „uruchom potrzebne testy” bez polecenia zniknęło. Kroki `planning`, `plan-fix` i `plan-review` dostają jedną
linię informacyjną `Kontrakt testów projektu (informacja dla planu): …` (`plan_tests_line`) ze źródłem
i poleceniami efektywnego kontraktu (`changed` z opisem `{base}`, `full`) albo z informacją, że kartki odeślą do
zasad projektu. Polityka planowania nie ma bazy ani `testPolicy` w zdarzeniu (nie jest wymogiem kroku ani źródłem
zgód); wznowienie zastępuje linię, a format `plan-review.json` i reguły zatwierdzania się nie zmieniają.

#### Zgoda na polecenia kontraktu (PT-280)

Autor ticketu, poprawek, recenzent, krok domknięcia i rozwiązania konfliktu mogą uruchomić polecenie
zatwierdzonego kontraktu testów bez wyniku `denied` i bez pytania. Regułę ma jedna funkcja,
`testpolicy.contract_grants(policy, denied)` → `ContractGrants(commands, notes, fix)`; `_launch` woła ją przy każdym
starcie i wznowieniu z **tej samej** `TestPolicy`, z której powstała kartka kroku (`step_test_policy`), więc tekst
zgody to dokładnie `policy.changed` / `policy.full` z zasady testów tej kartki.

- **Pochodzenie zgody.** Pole trafia na listę tylko ze źródłem `profile` (`changed_source` / `full_source`),
  czyli gdy `approval_for_step` dla tego kroku dało `approved` przy budowie polityki: odcisk zgodny z treścią
  pliku w tej chwili i brak kolizji ścieżek magazynu. Kontrakt zmieniony przez planistę, autora, inny krok,
  ręcznie albo cofnięciem commita (odcisk różny), brak lub uszkodzenie magazynu odcisków i kolizja ścieżek dają
  listę bez poleceń kontraktu, a kartka mówi, że kontrakt nie jest zatwierdzony; uruchomienie idzie zwykłą
  ścieżką `denied`. Zgody nie zapamiętujemy między krokami — każde wznowienie liczy ją od nowa. Polecenia
  wykryte z `Makefile` (źródło `detected`, także `legacy`) i zasada ogólna nie dają zgód.
- **Literalne polecenie.** Zgodę dostaje tylko polecenie, dla którego `project_tests.grant_note` jest puste:
  pojedyncze (`single_command` — bez `;`, `&&`, `|`, `$`, odwrotnego apostrofu, podpowłoki, `env`, przypisań
  innych niż język/strefa) i bez znaku `*` po podstawieniu bazy. W regule Claude daje to `Bash(<polecenie>)` bez
  wariantu `… *`, a w `is_allowed` — dokładne porównanie argumentów, więc ani dodatkowe argumenty, ani inne
  polecenie (np. `pytest -x tests/other` przy `full: pytest *`) nie przechodzą. Polecenie z `*` zostaje
  wymogiem kartki bez zgody, z tym samym komunikatem co `testsGrantNote` w JSON projektu (okno projektu).
- **Baza.** `changed` z nierozwiniętym `{base}` (brak przypiętej bazy) nie trafia na listę; `full` trafia.
  SHA bazy jest wstawiane przez `shlex.quote` w `effective_tests`, krok konfliktu ma bazę scalanego ticketu.
- **Pierwszeństwo zakazów.** Polecenie pasujące do zakazu projektu (`denied-commands`) albo Tide
  (`TIDE_DENIED`, `make install`) — także z prefiksem `LC_ALL=…` (`forbidding_rule`) — nie jest dokładane,
  a kartka ostrzega, że kontrakt testów wymaga poprawy.
- **Gdzie widać zgody.** Polecenia dochodzą do listy kroku (`_background_step(contract_commands=…)` →
  `claude_tools([*allowed-commands, *zgody], …)`), a nie do listy zapisanej w projekcie (`allowed-commands` się
  nie zmienia). Kartka ma je w sekcji „Polecenia projektu w tym kroku”, w bloku
  `<TIDE_CONTRACT_COMMANDS>…</TIDE_CONTRACT_COMMANDS>` (zgody, polecenia bez zgody z komunikatem, ostrzeżenie
  o poprawie); wznowienie usuwa blok poprzedniej próby i dokłada aktualny. Bez zgód i uwag blok nie powstaje
  (tekst master). Zdarzenie startu kroku ma `contractCommands`. Piaskownica i kontrakt `outside` się nie zmieniają
  (sieć i Docker nadal przez `outside`); krok w pudełku dostaje ten sam blok w kartce.

### Katalogi danych projektu tylko do odczytu (PT-246)

W „Projekty i tagi” pole „Katalogi tylko do odczytu” przyjmuje po jednym katalogu w wierszu,
np. `~/.cache/hud-timeline`. `UpsertProject` przyjmuje `readDirs: ["/pełna/ścieżka", "~/katalog"]`;
migawka projektu zwraca `readDirs` jako listę ścieżek kanonicznych. Brak pola w starym projekcie
oznacza pustą listę. Pusta lista w żądaniu usuwa udostępnienie, pominięcie pola zachowuje ustawienie.
Zapis w pliku projektu: `read-dirs` (JSON); wewnętrzne `read-dir-bindings` zachowuje powiązania dowiązań.

Limit wynosi 32 wpisy. Tide usuwa duplikaty, rozwija `~/` względem użytkownika usługi i rozwiązuje
dowiązania. Nie tworzy brakujących katalogów. Odmawia korzeni systemu, punktów montowania,
katalogu domowego i jego przodków, katalogów sekretów (m.in. `.ssh`, `.gnupg`, `.aws`, `.azure`,
`.kube`, `.codex`, `.claude`, `.config`, portfel kluczy i stan Tide) oraz nakładania z obszarem zapisu
kroku. Ścieżki ze znakami wzorców uprawnień są odrzucane. Token aktualnego punktu modelu również
nie może leżeć w udostępnionym katalogu. Błąd ustawienia nie zamienia się po cichu w inne uprawnienie.

Każdy start i wznowienie ponownie sprawdza istnienie katalogów i zapamiętane cele dowiązań.
Podmiana celu daje czytelną odmowę; trzeba usunąć stary wpis i świadomie udostępnić nowy cel.
Katalogi dostają wyłącznie kroki projektu oraz kroki już uprawnione do odczytu tego projektu jako
dodatkowego projektu karty/zlecenia. Nie są dopisywane globalnie do innych kart ani do poleceń hosta.
Zmiana ustawienia działa od następnego uruchomienia, również przy wejściu do tej pracy w Konsoli.
Już działający proces zachowuje poprzedni profil; usunięcie wpisu go nie zatrzymuje.

Claude otrzymuje `allowRead` i **`denyWrite` profilu systemu plików**, obok zakazu narzędzi Edit.
Codex zachowuje `workspace-write`: dane nie trafiają do `--add-dir` ani `writable_roots`, a domyślne
obszary zapisu `/tmp` i `TMPDIR` są wyłączane dla tego profilu. Zachowuje to istniejący ogólny zakres
odczytu Codexa; `readDirs` nie jest nowym mechanizmem ukrywania reszty dysku. Wejście do Konsoli dla
tej pracy używa wspólnego profilu i zachowuje go także po usunięciu ostatniego wpisu. Nie powstaje
nowe pudełko K-38 ani nowa ścieżka wykonywania poleceń poza piaskownicą.

Dowód działania granicy: `daemon/tests/test_project_read_dirs_native.py`, włączany przez
`TIDE_READ_DIRS_NATIVE=1`. Test używa natywnego Codex `sandbox linux` i natywnego Bash Claude
z lokalną atrapą API (bez modelu, prawdziwych kluczy, hooków ani MCP). Raporty w
`build/test-results/read-dirs-native/{claude,codex}-proof.json` osobno pokazują udany odczyt oraz
niezerowe kody prób zapisu, utworzenia, usunięcia i zmiany nazwy, bezpośrednio i przez dowiązanie
z worktree. Niedostępność piaskownicy po włączeniu testu jest błędem, nie udanym testem ani pominięciem.
Próby wymagające zagnieżdżenia piaskownicy należy zgłosić w kroku jako `outside`.
Jeśli Codex jest zainstalowany jako Snap, próba używa natywnego programu z tego samego
zainstalowanego pakietu (`/snap/codex/<wersja>/bin/codex`). Wrapper `snap run` może odmówić startu
w jednostce `tide-outside`, zanim zostanie utworzony profil. Test sprawdza granicę `workspace-write`
samego programu; nie jest testem dodatkowych ograniczeń Snap ani zgodności jego wrappera z systemd.
Nie zmienia to produkcyjnego wyboru programu. Artefakty `{claude,codex}-command.json` zapisują
dokładne argv wykonanego testu obok dowodu operacji.

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
| `projects` | `[{slug, name, repo, repoMissing, tags, aiDocs, aiDocsDefault, tool, entry, mode, description, docsDir, cards, path, youtrack, youtrackSync, reviewIndependence, reviewIndependenceClasses}]` (`youtrackSync` — klucz ustawiony i synchronizacja włączona; `reviewIndependence` — surowa wartość z pliku, nieznana czytana jako `free`) |
| `cards` | `[{id, num, title, project, projectName, tags, resolvedTags, column, boardColumn, order, note, created, createdTs, updated, updatedTs, moved, movedTs, youtrack, spec, session, branch, check, entry, mode, tool, worktree, launched, launchedTs, branchSlug, projects, extraProjects, description, derived, path}]` w kolejności kolumn i `order`; `branchSlug` — `k-12-<opis>`, część proponowanej gałęzi bez narzędzia; `extraProjects` — dodatkowe projekty rozwiązane `[{slug, name, repo, repoMissing}]` (bez nieznanych slugów) |
| `repos` | repozytoria z katalogu projektów, które nie są jeszcze projektami: `[{name, path, aiDocs}]` (domyślne `ai-docs`) |
| `errors` | `[{path, message}]` — złe pliki; także konflikty z YouTrackiem (PT-7, z pustym `path`) |
| `queue` | stan kolejki poleceń poza piaskownicą (K-39 #4, PT-374; tylko usługa z przepływem karty, po pierwszym ticku — stara usługa nie ma pola): `heavy` `{limit, running: [{step, card, ticket, command, since, kind, exclusive}], waiting: [{…, reason}], load: {avg1, cores, threshold, error}}` (`kind`: `outside` albo `merge-check`; `exclusive` — polecenie wymagające ciszy, K-39 #6; `waiting` w kolejności startu, `reason` to zdanie usługi, także o ciszy; `load` — jeden odczyt obciążenia maszyny na tick, `error` niepusty przy błędzie odczytu), `outside` (lekkie) `{perProject, total, occupied: {total, projects: {slug: n}}, running: […], waiting: […]}` (`occupied` liczy trwające razem z ciężkimi, bo liczą się do limitów; wpisy lekkich mają `project`), `limits` `{heavyLimit, providerSlots, providerSlotsDefault}`. Część `steps` dochodzi w #10 |
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
Tylko specyfikacja (PT-203) przekazuje do `RenderLaunch` parę `baseRef` (pełne
`refs/heads/…` albo `refs/remotes/…`) i `baseCommit` (oczekiwany SHA) projektu karty.
Wspólna polityka baz odmawia po ruchu/usunięciu refu oraz dla refów symbolicznych;
okno wymaga odświeżenia wyboru. Polecenie zawiera ref/SHA oraz instrukcje `git show`
i `git ls-tree` do czytania także plików nieobecnych w checkoutcie. Dodatkowe projekty
zachowują swoje bazy i odczyt. Karta przechowuje osobne `spec-revision-repo`,
`spec-revision-ref`, `spec-revision-commit`, widoczne w migawce i odpowiedzi jako
`specRevision: {repo, ref, commit}`. To kontekst wejścia, bez initialBase,
`card.started`, numeracji ticketów, gałęzi/worktree i przełączania checkoutu.
Powtórne RenderLaunch bez jawnego wyboru waliduje zapisany kontekst; starsze wywołania
bez kontekstu zachowują dotychczasowe działanie. Sposób zapisu specyfikacji pozostaje ten sam.

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

**Drugie zatwierdzenie w trakcie (PT-407, K-102).** Monitor przepływu trzyma zbiór kart z zatwierdzaniem planu
w kolejce zmian albo w toku (wątek główny, od przyjęcia do doręczenia odpowiedzi; czyszczony przy sukcesie,
`FlowError`, wyjątku i odmowie „usługa się zamyka”). Wchodzą do niego `ApproveCardPlan` (plan i różnica planu —
ta sama metoda) oraz `ApproveCardEnvironment`, które zatwierdziłoby plan. Duplikat dowolnej z tych metod dla tej
samej karty dostaje od razu `{"ok": true, "id": "K-n", "approving": true, "message": "Zatwierdzanie trwa…"}`
i nie czeka w kolejce. Migawka: `derived.flow.plan.approving` to `plan.approval_requested` bieżącej propozycji bez
`plan.approved`, bez nowszego nieudanego wyniku `reserve` tej propozycji i bez wstrzymanej decyzji o bazie (PT-208);
`approvalError` to treść ostatniego błędu rezerwacji albo powód „Zatwierdzanie wstrzymane: czeka decyzja o bazie
karty”. `status` nie dostaje nowych wartości. Zatwierdzanie różnicy planu nie zapisuje `plan.approval_requested`,
więc migawka nie pokazuje dla niego `approving`; jego koniec klient poznaje z odpowiedzi wywołania. W trakcie
faza karty to „zatwierdzanie planu w tle”, a decyzja `plan-approval` nie jest oferowana. Znane ograniczenie:
błędy `approve_plan` po zapisaniu prośby, które nie trafiają do dziennika jako wynik `reserve` (projekt usunięty
z konfiguracji, `OSError` przy przenoszeniu kartek ticketów), nie kończą „trwa” — tick ponawia próbę, a karta
pokazuje „zatwierdzanie planu w tle” bez `approvalError` i bez `plan-approval`, dopóki przyczyna nie ustąpi.

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

**Projekty zlecenia (K-33 #4, PT-115, decyzja 94).** Żądanie Planowania (Lekkiego i Pełnego) może mieć
`projects` — dodatkowe projekty zlecenia (slugi albo ścieżki repozytoriów, jak `projects` w UpdateCard); różniące się
od karty trafiają do `projects` karty. Usługa sprawdza każdy projekt zlecenia przed pierwszym zdarzeniem: projekt bez
repozytorium albo z niedostępnym kończy zlecenie błędem `no-repo` z nazwą projektu. `card.started` ma `projects`:
`[{slug, name, repo, prefix, tracker, trackerProject, configured, source, main}]` (pierwszy to projekt karty;
`configured: false` — repozytorium numeruje w YouTracku, a projekt Tablicy nie ma klucza YouTracka). Krok planowania
pracuje w worktree karty, a repozytoria pozostałych projektów dostaje tylko do odczytu: Claude przez `--add-dir`
z regułami `Edit(//repo/**)` w `--disallowedTools` (w tle także `allowRead` piaskownicy, `allowWrite` tylko `out`),
Codex bez `--add-dir`. Polecenie wymienia projekty z repozytorium, prefiksem, trackerem i źródłem numerów i żąda
planu standardu 1.3 z kolumną `Projekt`. Sprawdzanie propozycji odrzuca projekt spoza zlecenia, gotowe ID z prefiksem
innego projektu, cykl blokerów, więcej niż 6 projektów i projekt YouTracka bez trackera na Tablicy; komunikat zaczyna
się od ID wiersza. `plan.proposed` i `derived.flow.plan` mają wtedy `projects` (ze źródłem numerów), a wiersze pole
`project`. `ApproveCardPlan` planu z ticketami w kilku projektach odpowiada `group-approval` („zatwierdzanie grupy kart
— w następnej wersji”) bez zapisu w dzienniku. Wejścia `now` i `import` odrzucają niepuste `projects` (`single-project`).

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
`in` — kartka, ustawienia, gitconfig; `out` — plik wyniku i notki). Krok to (przez `Runner`, niżej) `agentcmd.background()` +
`Background.start()` (PT-76): `claude -p` albo `codex exec` w jednostce `systemd-run --user --scope`
`tide-step-<K-12>-<PT-7>-<n>`, z piaskownicą i kartką na stdin. Claude dostaje
`--permission-mode acceptEdits`, Codex opcjonalny `model_reasoning_effort`. Inny dostawca dostaje token
z pliku poza argumentami programu i dziennikiem; lokalny nie przechodzi do chmury. Bez Konsoli.

**Runner** (`tasks/runner.py`, PT-180). Silnik nie woła `agentcmd` ani `stepstop` do startu i zatrzymania kroku,
tylko wstrzyknięty `Runner` (`FlowEngine(..., runner=)`, domyślnie `HostRunner(systemd_run)`): `start(step,
before_start=)` składa krok (`agentcmd.background`), przygotowuje katalogi i pliki, woła `before_start` (kopiowanie
zrzutów) i uruchamia program — zwraca `Started(background, proc)`, a `proc` (Popen) trafia do `procs` silnika;
`kill(step, proc=, grace=)` zatrzymuje jednostkę z wpisu dziennika (`stop_step` dostaje go jako `kill=`, więc commit
WIP i wiadomości zostają jak dotąd); `env(step)` zwraca środowisko programu kroku (`agentcmd.background(step).env` — ta sama ścieżka składania co w `start`, z
zamianami punktu lokalnego, bez sekretów z `secret_env`). Krok Konsoli (`_launch_full_planning`, `resume_interactive`:
`agentcmd.interactive` + plik zadania) nie pasuje do umowy `start → Background` i zostaje poza `Runner`. `HostRunner`
to dzisiejszy sposób bez zmian: argv i środowisko pilnuje złoty plik `tests/fixtures/runner/host_argv.json`
(zapis sprzed refaktoru). Format wpisów kroku (`unit`, `gitDir`, `stepDir`) i odczyt stanu po restarcie usługi
(`stepstop.unit_state`, `read_exit_code`) bez zmian, więc kroki uruchomione przed aktualizacją są dalej rozpoznawane.
Wyboru runnera nie ma — to miejsce pod kolejny runner (pudełko karty).

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

**Pamięć ścisłego odczytu (K-105 #2, PT-411).** Ścisły odczyt historii karty (`Journal.read_complete`: pełne wiersze,
schemat, kolejny `seq`, ta sama karta, znana tożsamość startu procesu; błąd = unknown) pamięta zdarzenia dla wersji
dziennika (`Journal.version`, `JournalVersion`): źródło (kopia usługi albo plik w Tablicy), `st_dev`, `st_ino`,
`st_size`, `st_mtime_ns`, `st_ctime_ns` z `fstat` otwartego deskryptora, licznik zapisów tego procesu (`generation`)
i ostatni `seq`. Niezmieniony dziennik kosztuje `open` i `fstat`; trafienie zwraca nową listę tych samych obiektów
zdarzeń (zdarzenia są tylko do odczytu, także w fazie ticku `HistoryPhase`). Przy tym samym i-węźle, większym rozmiarze
i niezmienionym ostatnim zapamiętanym wierszu (porównany skrót bajtów w tym samym miejscu; pamięć nie trzyma tekstu)
odczyt doczytuje tylko nowe bajty i sprawdza je tą samą regułą. Każda inna zmiana — ucięcie, podmiana pliku (nowy
i-węzeł), zmiana treści bez zmiany rozmiaru, także z przywróconym mtime (`st_ctime_ns`), zapis tego procesu — daje
pełny odczyt. Błąd (pełny albo przyrostu) usuwa wpis, więc unknown zostaje przy każdym kolejnym odczycie, dopóki plik
jest uszkodzony. Tick na początku usuwa wpisy kart spoza rejestru (`cards_complete`).

*Ograniczenie przyrostu.* Przyrost sprawdza tylko stan pliku i ostatni wiersz: edycja wcześniejszych wierszy w miejscu
połączona z dopisaniem (ten sam i-węzeł, większy rozmiar, zachowany ostatni wiersz) nie jest nim wykrywana. Dlatego
wpis pamięta chwilę ostatniego pełnego odczytu, a po `FULL_READ_PERIOD` (10 min, zegar `Journal.monotonic`) odczyt
czyta plik w całości zamiast trafienia albo przyrostu; przyrost nie przesuwa tej chwili. Do tego czasu ograniczenie
dotyczy tylko odczytów bez skutku: porównania przed zapisem (`Journal.unchanged`, `HistoryPhase.unchanged`), zapisy
(`append`, `append_complete`) i kontrole wycofania (`rollback`) czytają świeżo (`Journal.read_fresh` — pełny ścisły
odczyt z pominięciem pamięci), więc zapis oparty na takiej historii jest odmówiony (`HistoryChanged`). Pełne odczyty
liczy granica `Journal._read_strict` (pełny odczyt `read_complete` i `read_fresh`); `Journal.check` jej nie używa —
ma własną pamięć opisaną niżej.

**Pamięć kontroli zgodności (K-105, PT-416).** `Journal.check` (dziennik w Tablicy ↔ kopia usługi, w każdym ticku dla
każdej karty) pamięta parę stanów obu plików według tej samej reguły pięciu pól (`st_dev`, `st_ino`, `st_size`,
`st_mtime_ns`, `st_ctime_ns`). Ta sama para kosztuje dwa `stat` i daje wynik bez ostrzeżenia, bez odczytu treści
i bez żadnego skutku. Każda zmiana któregokolwiek pola któregokolwiek pliku (revert z nowym i-węzłem, edycja w miejscu
z przywróconym mtime, `chmod`, `touch`) daje pełne porównanie treści obu plików w tym samym wywołaniu; odtworzenie
z kopii następuje wyłącznie po nim — pamięć tylko pomija porównanie i niczego nie autoryzuje. Para trafia do pamięci
po porównaniu zgodnym, obramowanym dwoma równymi `stat`, albo po odtworzeniu potwierdzonym tak samo (świeży odczyt
Tablicy równy kopii między dwoma równymi `stat`, kopia bez zmian od porównania); zmiana Tablicy z zewnątrz tuż po
odtworzeniu nie zostaje zapamiętana jako zgodna. Błąd `stat` albo odczytu, brak pliku i nieudane odtworzenie usuwają
wpis karty, a brak kopii daje jak dotąd odbudowę kopii z Tablicy bez pary. Zapis tego procesu do dziennika i początek
ticku dla kart spoza rejestru (`forget_except`) usuwają wpisy. Odczyty treści liczy granica `Journal._check_read`.
Ograniczenie jak przy kluczu wersji: zmiana w miejscu bez zmiany rozmiaru tuż po porównaniu, w obrębie jednego kwantu
zegara znaczników czasu jądra, nie zmienia żadnego z pięciu pól.

**Pamięć historii procesów i stanu procesu karty (K-105 PT-415).** Nad pamięcią ścisłego odczytu `Journal.derive`
pamięta wartość wyliczoną z historii karty: wynik kontroli tożsamości procesów i stan procesu karty
(`trialhold._checked_state` — jeden `fold` z modelem `process_identities`; nieznana albo niespójna tożsamość =
ValueError). Kluczem jest wpis pamięci ścisłego odczytu i jego `JournalVersion` (z `st_ctime_ns` i `generation`);
trafienie wymaga, by odczyt zwrócił zdarzenia tego samego wpisu, więc nowa wersja, przyrost, okresowy pełny odczyt,
zapis tego procesu i luźna wersja dają nowe wyliczenie. Pamięć nie trzyma tekstu ani własnej kopii zdarzeń — tylko
odwołanie do wpisu PT-411 i stan. Błąd odczytu, `stat` albo kontroli usuwa wpis karty i nie jest pamiętany ani jako
historia, ani jako „brak zmian”. Wpisy znikają razem z pamięcią ścisłą (`_invalidate`, `forget_except`).

`trialhold.read_process_history(journal, card)` zwraca historię z tej pamięci, a `trialhold.process_state` —
(historię, kopię stanu z własnymi kontenerami pól; wartości zagnieżdżone wspólne i tylko do odczytu, jak w
`Journal.state`, wspólna funkcja `state.copy_state`). Z pamięci korzystają wyłącznie odczyty bez skutku: faza ticku
(`HistoryPhase` jest widokiem jednego ticku nad pamięcią; zapamiętany błąd fazy nadal rzucany w obrębie fazy),
bariera `_outside_history(card)`, inwentaryzacja K-38, `_recover_started_steps` z fazy (zapis przez `Journal.unchanged`),
`boxstep.recover_network` z fazy (model tożsamości i stan z pamięci, mutacje po `HistoryPhase.unchanged`; nowy `fold`
tylko dla historii z pominiętymi próbami K-38; bez fazy — odtworzenie zamiaru startu — świeżo), cykl życia pudełka
przy `box.enabled=true` (`box/lifecycle.tick`, `active_steps`, `packs.cleanup` — ta sama pamięć ścisła PT-411 co przed
PT-415, teraz bez nowego `fold` co tick), obserwacja kroku co tick (`_poll`, `_guard`, `_advance`,
`_recover_console_openings`) oraz czekanie na polecenie outside i próbę testów po scaleniu (`_outside_ready`,
`_merge_check_run`, `_outside_recheck`, wejście `_resume_step`). Wariant `fresh=True` (świeży pełny odczyt
`Journal.read_fresh` i nowe złożenie, bez czytania i zapisu pamięci) mają bramki przed skutkiem: `_process_check`
przed startem, zatrzymaniem i wznowieniem procesu (domyślnie), `_outside_history` z krokiem lub poleceniem (zgoda,
start, wynik i zapis `outside.finished`, zapisy `_outside_recheck` i automatycznej zgody — `consent.record_automatic`
z `gate`), próba testów po scaleniu (wynik z zapisanego zakończenia, start pod blokadą przed Popen, zapis
`outside.finished`/`merge.check-finished`), zatrzymanie jednostki outside po końcu albo przekroczeniu czasu
(`_outside_collect` z `confirm` — świeży odczyt tylko po zakończeniu uruchomienia, nie przy czekaniu), wejście Konsoli,
przekazanie, ręczne domknięcie, diagnostyka K-38 i `public_inventory` przed zapisem.
Inwentaryzacja K-38 liczy pozycje z pamięci, a gdy trzeba coś dopisać (`k38.handoff`, `k38.inventory`), powtarza całą
inwentaryzację na świeżym odczycie i dopiero ona zapisuje. Odtworzenie startu bez fazy czyta świeżo. Rozgrzany tick bez
zmian w dziennikach nie woła modelu tożsamości ani `fold` (`_pending_main_merge` czyta projekcję `Journal.state`).
Pamięć usługi mierzy PT-414 (punkt odniesienia bez progu).

**Pętla odtwarzania na początku ticku (K-105 PT-459).** `FlowEngine._replay_starts` obejmuje każdą kartę z dziennikiem
(także zakończoną) i bierze jej historię oraz stan procesu karty z fazy ticku (`trialhold.process_state` z
`HistoryPhase` — pamięć PT-411/PT-415), a nie z własnych pełnych odczytów: ścisła reguła odczytu zastępuje dawny skan
`order.` po `journal.read` i kontrolę `startorder.events` (pełne wiersze, kolejny `seq`; poza pętlą `startorder` przy
starcie zlecenia czyta plik świeżo jak dotąd), a `BaseGeneration.recover` i `_project_base` dostają ten sam odczyt
(`observed`). Błąd odczytu albo nieznana tożsamość karty wstrzymuje odtwarzanie tylko tej karty (tick pomija ją
też dalej). Pamięć nie autoryzuje zapisu: zakończona generacja bazy (aktywacja i wynik w historii) nie jest
wznawiana, każde inne odtworzenie generacji zaczyna się od `Journal.unchanged` z odczytem z pamięci (dopisanie po
odczycie → `HistoryChanged` przed operacjami git i zapisem; generacja w toku płaci więc jeden świeży odczyt na tick),
a projekcja bazy na Tablicę porównuje historię świeżo tylko przed faktycznym zapisem pól (`project_card_base(guard=…)`).
Po zapisie odtworzenia faza czyta kartę ponownie (licznik zapisów), więc projekcja widzi własny zapis.

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

**Lista lokalnych baz (PT-209).** `ListCardBranches` zwraca wyłącznie `refs/heads/` z rekordami
`{ref, name, kind: "local", remote: "", commit, selectable, reason}`. `name` zachowuje pełną nazwę,
`commit` jest pełnym SHA, `reason` zawiera powód ewentualnej odmowy. Filtr `text` dopasowuje dowolny
fragment bez rozróżniania wielkości liter. `mainRef` jest pełnym refem według wspólnej polityki
głównej lub pustym napisem z ostrzeżeniem. Główna jest pierwsza, jeśli pasuje do filtra.
`recentBases` zawiera skróty udanych startów i korekt, a `recentTargets` dla celu merge — udanych scaleń
(opis PT-214 powyżej). `hasMore` wymaga zawężenia zapytania, bez kolejnych stron.
Okno startu pozwala wybrać bazę w „Od razu”, „Mam plan” i Planowaniu, wysyła pełny ref i SHA z wyniku listy oraz osobną
nazwę nowej gałęzi karty `cardBranch`. Niepoprawna lub zajęta nazwa jest odrzucana. Brak pola zachowuje
automatyczną nazwę starszych klientów. Wybór dotyczy tylko projektu karty; dodatkowe projekty zachowują
dotychczasowe bazy i odczyt. „Tylko specyfikacja” ma nieaktywny wybór do czasu obsługi wybranej rewizji.
Review, poprawki i zatwierdzanie planu używają tej samej gałęzi karty; kolejna fala wychodzi z jej aktualnego
stanu po integracji blokerów. Przejęcie planu kopiuje artefakty do dziennika, bez zmiany źródła i jego numerów.

**Podpowiedzi tekstowe bazy (PT-196).** `SuggestCardBase` czyta opis zapisanej karty i osobno tekst
formularza (do 200000 znaków). `revision` to nieujemny numer wersji formularza. Analiza rozpoznaje pełne,
istniejące nazwy i refy ze spisu lokalnego gita, z rozróżnieniem wielkości liter i granic nazw, również
w backtickach oraz przykładach `git switch`/`checkout`. Nie rozwiązuje tekstu jako rewizji gita.
Wskazana nieistniejąca nazwa jest ostrzeżeniem. Lokalny i zdalny ref o tej samej nazwie pozostają
odrębnymi kandydatami; wiele wskazań daje ostrzeżenie, bez rozstrzygnięcia za użytkownika.

Kandydat zawiera `{ref, name, commit, source, evidence, strength, selectable, reason}` (oraz metadane
gałęzi `kind`, `remote`). Dla tekstu `source: text`, `strength: explicit`, a `evidence` to lista
`{field: description|text, line, text, match, start, end}`: numer wiersza od 1, wycinek wokół trafienia
(do 512 znaków) i zakres widocznego trafienia w tym wycinku od 0, z końcem wyłącznym. Bardzo długa
nazwa może być przycięta w dowodzie; kandydat zachowuje pełną nazwę i ref. Odpowiedź zawiera najwyżej
32 kandydatów, po 5 dowodów na ref łącznie z obu pól, i 20 ostrzeżeń o nieznanych nazwach (nazwa do
256 znaków), poza komunikatem o konflikcie i ograniczeniu listy. Kontekst polecenia obejmuje najwyżej
80 znaków przed tokenem. Dowody są zwykłym tekstem. Historia plików i linki PR/MR
mają osobne przyciski i wymagają jawnego żądania użytkownika.

Okno zlecenia analizuje tekst po krótkiej pauzie. Odpowiedź przyjmuje tylko dla bieżącego tagu,
revision, karty, projektu i repozytorium. Zmiana tekstu, kontekstu lub wyboru unieważnia oczekującą
odpowiedź. Podpowiedź nigdy nie zmienia wyboru samoczynnie. „Użyj <pełny ref>” wypełnia istniejące pole
bazy z SHA; dopiero `StartCard` utrwala decyzję. Błąd analizy nie blokuje ręcznego startu. Dla utrwalonej
bazy podpowiedzi są wyłączone. Sam odczyt nie zapisuje karty ani dziennika i nie mutuje gita.

**Lokalna historia plików (PT-197).** Przycisk „Sprawdź lokalną historię plików” wywołuje ten sam
kontrakt z `scope: files`. Analiza działa w istniejącym wątku roboczym i nie blokuje ręcznego startu.
Ścieżki pochodzą z opisu karty i polecenia: nazwy z ukośnikiem lub kropką, a w cytatach/backtickach
także nazwy bez rozszerzenia i ze spacjami. Ścieżki są względne wobec katalogu repozytorium;
bezwzględne przyjmujemy tylko wewnątrz tego katalogu. Traversal i globy są pomijane z ostrzeżeniem.
Nie odczytujemy wskazanych plików z dysku, nie podążamy za dowiązaniami; plik może istnieć wyłącznie
w drzewie gałęzi feature. Sam basename jest używany dopiero po jednoznacznym dopasowaniu w całym
analizowanym zbiorze drzew. Wieloznaczny skrót wymaga podania ścieżki od katalogu repozytorium.
Plik w katalogu głównym można wskazać dokładnie jako `./config.py`, także gdy gdzie indziej jest jego imiennik.

Zakres wynosi najwyżej 20 różnych ścieżek, 100 lokalnie dostępnych refów gałęzi (lokalnych i remote,
bez symbolicznych), uporządkowanych po dacie commita końcowego, 200 najnowszych osiągalnych commitów
na gałąź oraz 5 sekund łącznie. Każde wywołanie gita dostaje pozostały czas. Najpierw ograniczamy
zbiór commitów bez filtra ścieżek, następnie czytamy zmiany plików wyłącznie w tym zbiorze, bez
dalszego przechodzenia historii. Ścieżki trafiają za `--` z literalnymi pathspecami, bez powłoki.
Nie ma fetch, checkoutu ani indeksowania całej historii. Przy limicie lub błędzie wynik pozostaje
częściowy; ostrzeżenie wyjaśnia, że brak wskazania nie wyklucza gałęzi. Odpowiedź zawiera również
`analysis: {partial, paths, branches, limits: {paths, branches, commitsPerBranch, seconds}}`.

Kandydat zawsze ma `source: files`, `strength: heuristic`, końcowy ref/SHA oraz dowody
`{path, commit, timestamp, time}` (czas committera UTC). Dowód wymaga obecności ścieżki w drzewie
końcowego SHA i dotknięcia jej w ograniczonej historii. Ranking: liczba udowodnionych ścieżek
(`matchedPaths`), następnie najnowszy czas ich dotknięcia (`lastTouch`); data niezwiązanego commita
końcowego nie podwyższa oceny. Identyczna ocena daje identyczny `rank`, także dla wspólnego przodka
kilku gałęzi; kolejność alfabetyczna jest wyłącznie porządkiem prezentacji. Wspólna lista pokazuje
pozycję, ścieżkę, dotykający commit i czas. Dopiero „Użyj <pełny ref>” zmienia ręczną bazę, a start
przekazuje wybrany SHA do istniejącego mechanizmu utrwalania bazy.

**Podpowiedzi PR/MR (PT-198).** „Sprawdź linki PR/MR” wysyła `scope: links`; automatyczna analiza tekstu
nie odpytuje hostingu. Adaptery `gh api` i `glab api` korzystają z istniejącego logowania CLI, bez konfiguracji
integracji w Tide. Rozpoznają wyłącznie HTTPS `/owner/repo/pull/N` i `/grupa/podgrupa/repo/-/merge_requests/N`
na hoście i w projekcie zgodnym ze skonfigurowanym remote (SSH/scp/HTTPS oznaczają tę samą tożsamość).
Userinfo, query, obce projekty/hosty i inne rodzaje URL są odrzucane przed uruchomieniem CLI.
Aliasy remote przez `url.<base>.insteadOf` nie są rozwijane: np. `gh:team/proj` wymaga remote
z pełnym adresem SSH/HTTPS, inaczej link dostanie ostrzeżenie o niezgodności z remote.
Najwyżej 4 różne linki na analizę, wspólny termin 10 sekund dla wszystkich odczytów (także projektów
źródłowych/docelowych GitLaba) i 1 MiB łącznego stdout/stderr na wywołanie CLI. Po upływie terminu
wraca dotychczasowy wynik z ostrzeżeniem. Migawka karty, gałęzi i remote powstaje w szeregowym wątku
przepływu; hosting jest odczytywany w osobnej puli, bez blokad gita i dziennika. Jednocześnie mogą trwać
dwie analizy hostingu; kolejne dostają błąd zajętości bez kolejki sieciowej. StartCard i tick nie czekają na CLI.

Kandydat `source: links` dodaje `{host, project, sourceProject, sourceBranch, sourceCommit, targetBranch,
link, localCommit, stale}`. Target nigdy nie jest proponowaną bazą. Źródłowe repozytorium musi mieć
odpowiedni remote; lokalny ref musi wynikać z jego refspec fetch albo jawnej konfiguracji upstream lokalnej
gałęzi. Nakładające się mapowania refu z obcych repozytoriów są niejednoznaczne. Sama zgodność nazwy
lokalnej gałęzi z forkiem nie wystarcza. Brak źródła daje niewybieralną pozycję z `reason`.
Nie wykonuje się fetch. Rozbieżne SHA API i lokalnego refu są widoczne razem; „Użyj stanu lokalnego
<pełny ref>” przekazuje lokalny SHA do istniejącego wyboru bazy i `StartCard`.

Bramka `gh` wykonuje jeden ustalony GET HTTPS na wywołanie i odrzuca wszystkie przekierowania.
CLI używa klucza `http_unix_socket` z tymczasowego `GH_CONFIG_DIR/config.yml`; `hosts.yml` jest wyłącznie
dowiązaniem do istniejącego pliku konfiguracji użytkownika (bez kopiowania i odczytu sekretów przez Tide).
Tymczasowy `config.yml` zawiera też `version: "1"`: bez niego gh uruchamia migrację konfiguracji
i zapisuje `hosts.yml` przez dowiązanie. Gdy `hosts.yml` istnieje, a `config.yml` użytkownika nie ma
`version: "1"` (magazyn niezmigrowany), Tide nie uruchamia gh i zwraca ostrzeżenie; Tide czyta z
`config.yml` tylko klucz `version`.
Nieobsługiwany transport kończy się błędem przez zastępcze proxy bez dostępu do hostingu.
`glab` dostaje jawne `GLAB_PROXY` wskazujące lokalne proxy TCP oraz `GLAB_API_PROTOCOL=https`
i `API_PROTOCOL=https`. Proxy przyjmuje tylko `CONNECT` do ustalonego hosta na porcie 443; odrzuca obcy
host, inny port i zwykły HTTP bez połączenia wychodzącego. Nie odszyfrowuje TLS ani nie widzi tokena.
Przekierowanie HTTPS na tym samym hoście może być obsłużone przez `glab` wewnątrz tunelu; przekierowanie
na obcy host lub HTTP zatrzymuje proxy. TLS weryfikuje CLI (wymuszone `skip_tls_verify=false`).
Transport ma limit 2 MiB na tunel z narzutem TLS, niezależnie od limitu 1 MiB stdout/stderr i terminu analizy.
Poświadczenia nie są odczytywane przez `auth token`, zapisywane ani zwracane. Wynik zawiera tylko
dozwolone pola metadanych; stderr i surowy
JSON CLI nie trafiają do odpowiedzi ani dziennika. Opis i polecenie służą wyłącznie wyodrębnieniu linków,
nie są wysyłane do hostingu. Brak CLI, logowania, sieci, PR/MR, błędny JSON, limit lub timeout daje ostrzeżenie;
ręczny lokalny start pozostaje dostępny. Dla hostów z wymaganym zewnętrznym proxy należy wybrać bazę lokalnie;
adapter nie przejmuje konfiguracji pośrednika ani logowania.

**Baza startu karty (PT-194, PT-211).** Od razu, Mam plan (`entry: import`), propozycja z Konsoli
(`entry: plan`) i oba istniejące tryby Planowania korzystają z jednego rozstrzygnięcia bazy.
Żądanie każdego z tych wejść może zawierać `baseRef` (wyłącznie pełny lokalny
`refs/heads/…`) i `baseCommit` (oczekiwany pełny SHA). Przy pierwszym starcie bez `baseRef` działa dotychczasowy
`FlowGit.main_branch`: istniejące `tide.mainBranch`, następnie polityka master/main/develop.
Nie zmienia to konfiguracji projektu. Pod blokadą repozytorium ref jest rozwiązywany do SHA przed
rezerwacją numeru i `card.started`. Brak gałęzi, niepoprawny ref albo niezgodny oczekiwany SHA
odmawia startu bez zlecenia i numeru; użytkownik odświeża wybór i rozpoczyna nowy start. Tag, SHA,
wyrażenie rewizji i `HEAD` nie są lokalnym refem gałęzi. Nie ma zastępstwa przez checkout użytkownika.
Gałąź i worktree karty powstają od przypiętego SHA, bez upstreamu; późniejszy ruch refu go nie zmienia.
Ponowny start wykorzystuje dotychczasową gałąź karty. Bez `baseRef` dziedziczy bazę zapisaną
w dzienniku, nawet po ruchu lub usunięciu refu; nie rozwiązuje ponownie głównej. Ticket wychodzi
z aktualnego stanu gałęzi karty, zachowując dotychczasową pracę. Dla legacy z bazą unknown
start bez wyboru bazy jest dozwolony, jeśli gałąź karty istnieje; baza i `initialBase` pozostają
unknown, bez zgadywania historycznego SHA. Brak takiej gałęzi daje `no-base` z instrukcją jej
przywrócenia lub założenia nowej karty z wybraną bazą. Nowe zlecenie ma własne `baseVersion: 1`
także dla unknown; historyczne zdarzenia i ich wersja 0 pozostają bez zmian.
Jawny inny `baseRef` wymaga zgodności rozwiązanego refu i SHA z bazą dziennika. Formularz wznowienia
pokazuje utrwalony ref i SHA bez odpytywania listy; przekazanie tego samego refu zachowuje zapisany SHA,
nawet po ruchu albo usunięciu refu. Sam `baseCommit`
bez refu musi odpowiadać zapisanemu SHA znanej bazy. Inna lub nieznana baza przy jawnym wyborze
powoduje `base-mismatch` przed rezerwacją numeru i zapisem zlecenia. Zmiana bazy należy do osobnej korekty.

Źródłem bazy jest dziennik: `card.started` zapisuje `baseRef`, `baseBranch`, `baseCommit` oraz
`baseVersion: 1`. Snapshot udostępnia te trzy pola na karcie, a w `derived.flow` obiekty `base`
i `initialBase` o kształcie `{status: "known"|"unknown", baseRef, baseBranch, baseCommit}` oraz
liczbę `baseVersion` (również na karcie). Dla unknown trzy wartości są puste. Model i plik karty
mają tylko projekcję `base-ref`, `base-branch`, `base-commit`; nie zapisujemy płaskiego pola wersji.
UpdateCard odrzuca pola bazy i wersji. Snapshot bierze wartości z dziennika także przy nieudanym
zapisie projekcji; ponawiany zapis projekcji nie uruchamia zlecenia ani gita ponownie.
Projekcja nie dopisuje brakujących pustych pól ani nie zmienia plików kart w Gotowych i Archiwum.
Gdy karta nie ma wyliczanego stanu (np. wróciła ręcznie do Do zrobienia albo jest w Gotowych/Archiwum),
zachowuje `derived: null`. Jej pola `baseRef`, `baseBranch`, `baseCommit` i `baseVersion` na karcie
w Snapshot nadal pochodzą z dziennika, niezależnie od powodzenia zapisu projekcji.

Historyczne zlecenie bez wersji zawsze ma `baseVersion: 0`. Znana historyczna baza wymaga zamiaru
utworzenia gałęzi wskazującego ref i skutecznego wyniku z SHA oraz `existing: false`, z tego zlecenia.
Wynik zastanej gałęzi nie dowodzi SHA pierwotnego startu. Bez dowodu baza jest unknown; nie czytamy
dzisiejszej głównej i nie dopisujemy pól do starych zdarzeń.

Kontrakt korekty: pojedyncze skuteczne `card.base-changed` zapisuje atomowo `base`
(ten sam kształt trzech pól), `generation` (pełna mapa zasobów korekty PT-202), `previousBaseVersion` i
`baseVersion: previousBaseVersion + 1`. Replay przyjmuje przejście tylko z bieżącej wersji i znaną
nową bazą; powtórzenie tego samego zdarzenia nie zwiększa licznika. `initialBase` zachowuje start.
Nowe zlecenie ma własne 1. Podgląd, odmowa, błąd, ruch refu i naprawa projekcji nie zmieniają wersji.
Tokeny i przyszłe operacje scalenia/korekty korzystają wyłącznie z tego `baseVersion`. Wykonanie
korekty oraz mapowanie zasobów opisano poniżej. Historyczne zdarzenia z pustym `map` zachowują odczyt.

**Pusta generacja (PT-202).** `PreviewCardBaseChange` pozostaje odczytem. Wykonanie wymaga
`ResolveCardStep(id, {choice: "change-base", baseRef, baseCommit?, previewId, step?})`, gdzie `baseRef`
jest pełnym refem lokalnym lub znanym lokalnie remote, `previewId` pochodzi z aktualnego podglądu,
a `step` wskazuje pytanie agenta lub decyzję `RequestCardBaseChange`, jeżeli taka istnieje.
Sam wybór `baseDecision: "change"` nadal tylko otwiera podgląd. Odpowiedź i etykieta opcji nie są
poleceniem Git. Panel Tablicy i Sesji udostępnia osobne potwierdzenie tylko dla `canExecute: true`,
przekazując dokładny ref/SHA i identyfikator wyświetlonego podglądu. Odmowa lub oczekiwanie
na odzyskanie zasobów pozostawia dowody i odnośnik do szkicu w otwartym panelu.

Pod blokadą repozytorium usługa ponawia kwalifikację PT-201: dokładne SHA obu baz, wersję, pełną
mapę, dowiedzione punkty startu wszystkich gałęzi, brak wkładu, lokalnych zmian oraz procesów.
Zmiana daje `stale-preview` bez zamiaru i mutacji. Zamiar `intent(action: change-base)` przechowuje
stały `operationId`, odcisk, granicę dziennika, `oldBase`/`newBase`, obie wersje i kompletną `generation`.
Wiersz mapy zawiera `action`, `key`, `ticket`, `branch`, `worktree`, `base`, `oldBranch`, `oldWorktree`,
`oldCommit`, `ownerRef`, `archiveRef` i pełną listę ignorowanych ścieżek (katalogi zbiorczo).

Gałęzie/worktrees nowej generacji powstają poza starymi katalogami od przypiętego SHA. Transakcja
CAS tworzy gałąź, ref właściciela i ref zachowujący stary SHA; obca nazwa oznacza oczekiwanie.
Stare worktrees są blokowane, a nowe powstają z `--lock`. Ignorowane dane pozostają w starych
katalogach. `base-change.checkpoint` zapisuje pełną mapę po przygotowaniu każdego zasobu i przed
aktywacją. Ponowna kwalifikacja i kontrola całej nowej mapy poprzedzają jedno `card.base-changed`.
Fold wybiera pełną mapę i zwiększa wersję raz; częściowa/nieprawidłowa mapa nie zmienia żadnego pola.
`initialBase`, ID, treści, plan i blokery pozostają. Nie powstają puste merge ani replay commitów.

Restart odnajduje ten sam zamiar. Przed aktywacją dokańcza własne zasoby albo zapisuje
`base-change.waiting` z przyczyną i wykazem zachowanych zasobów, pozostawiając starą mapę.
Po aktywacji ponawia wyłącznie wynik i projekcję bazy, bez gita i kolejnej inkrementacji.
Retry z tym samym refem/odciskiem zwraca ten sam zamiar. Snapshot udostępnia `flow.generation`,
`flow.preservedGenerations`, `flow.generationWaiting` i `flow.baseValidationRequired`.
Stare refy, archiwalne SHA i katalogi nie podlegają zwykłemu sprzątaniu; cleanup nie przenosi ich danych.

**Skutki pustej korekty (PT-213).** Aktywacja zachowuje pole `validationRequired: true` jako część
kontraktu PT-202. Fold dowiedzionej kompletnej mapy odtwarza unieważnienia i uznaje walidację pustego
wkładu za spełnioną (`flow.baseValidationRequired: false`). Nie wymaga review fikcyjnego commitu.
Stare review, decyzje i domknięcie pozostają w dzienniku oraz historii, ale nie autoryzują nowego
SHA. Snapshot wystawia bieżące `flow.approvals` oraz osobne `flow.historicalApprovals` z oryginalnymi
SHA i wersjami. Historyczne kroki mają `historical: true`, bez EnterStep i transferu.

Nieodrzucony ticket rozpoczyna nowy krok w nowym katalogu z aktualnym ref/SHA/baseVersion, notką
korekty, dostępem do dawnych wyników i świeżą sesją CLI. Odrzucony ticket pozostaje odrzucony.
Nowa praca przechodzi istniejące testy i review swojego rzeczywistego SHA przed integracją; zasoby
generacji pozostają zachowane również podczas cleanup. Stara zgoda na polecenia do końca ticketu
nie przechodzi do nowej generacji. Stare kroki i zgody nie wracają przez recovery.

Karta wymaga nowego podsumowania na nowej bazie, także gdy zamknięto ją wcześniej po odrzuceniu
ticketu bez zmian. MergeCard i MergeCardToMain stosują tę samą bramkę domknięcia. Domyślny cel
w menu i porównaniu korzysta z bieżącej bazy, a token sprzed korekty daje `stale-preview`.
Remote nadal wymaga jawnego istniejącego lokalnego celu według wspólnej polityki celów.
Powtórny fold i restart nie uruchamiają przygotowania generacji ani kolejnej inkrementacji.

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

Wykonawcę poprawek (K-88/PT-312) wybiera kolejno: ręczna zmiana autora z panelu (`executorReason: override`),
eskalacja od rundy `ESCALATE_FIX_ROUND` (3; `escalated`) i dotychczasowy autor (`author`). Eskalacja dotyczy tylko
ticketu, którego klasa w zatwierdzonym planie nie jest `mocny` ani `lokalny` (brak klasy = `standard`). Jej cel to
pierwszy dostępny wykonawca klasy `mocny` preferowanego dostawcy zlecenia (w trybie mieszanym — dostawcy autora),
inny niż recenzent ocenianego i następnego review oraz inny niż autor; bez takiego poprawki robi autor, a zmiana
autora w katalogu nadal kończy krok błędem `changed-executor`. Autor to ostatni krok autora ticketu, który nie był
eskalacją ani pracą recenzenta, więc recenzent nie ocenia poprawek napisanych przez siebie. Ręczna zmiana autora
z panelu (`author.reassigned`) obowiązuje we wszystkich kolejnych rundach poprawek ticketu, aż do następnej takiej
zmiany. Wznowienie kroku `fix`: ręczna zmiana wykonawcy (`change-executor`, `transfer`) albo nowszy przepięty autor
zapisują `override`. Decyzje automatyczne (`auto`, `automatic`, `caretaker`), także po błędzie ponowienia lub
odmowie dostawcy, podlegają regule poprawek; sam wybór `change-executor` nie jest ręczną zgodą. Jeśli reguła
odrzuci proponowanego wykonawcę, krok wraca do zwykłego wznowienia. Wznowienie przelicza tę samą regułę —
ten sam wykonawca zachowuje sesję i powód, a gdy
reguła wskaże innego (np. stary krok pisał recenzent), wznowienie startuje świeżą sesją z powodem `author` albo
`escalated`. Automatyczny powrót do autora przechodzi tę samą kontrolę `changed-executor` co zwykłe poprawki. Próg
pytania o review (`reviewRoundsBeforeQuestion`) się nie zmienia.

Bramka startu review (PT-312, uzupełnienie właściciela 10.11): `_guard_review_launch` działa przed
rezerwacją kroku w `_launch` oraz przed uruchomieniem Konsoli w `_launch_full_planning`. Wejście do Konsoli
(`EnterStep`) w review zakończonym bez werdyktu (błąd, pytanie, zatrzymanie) nie wznawia starej sesji: to nowe
review przez `_launch_full_planning` z tą bramką i świeżą sesją (PT-330); przejęcie trwającego review i rozmowa
po werdykcie (`interactive`) działają jak dotąd. Kroki `fix` w Konsoli (przekazanie i wejście) zapisują
`executorReason` jak kroki w tle, więc ponowienie po ręcznym przekazaniu zachowuje `override`. Każde uruchomienie
review, także wznowienie, zaczyna osobną sesję. `reviewgate.authors` bierze z dziennika autorów ticketu
oraz wszystkich prób poprawek od poprzedniej rundy review; nieudany fix może zawierać WIP, a wznowienie
review tej samej rundy nie usuwa tych autorów z kontroli.

Warunek modelu jest skupiony w `reviewgate.permits(reviewer, authors, policy)`: `free` dopuszcza dowolny
model, `other-model` stosuje dotychczasowe `plans.independent_reviewer` (inny model lub wysiłek),
`other-family` porównuje parę dostawca/program z katalogu i zapisanych kroków. Polityka pochodzi z ustawienia
projektu ticketu `reviewIndependence` (PT-330, decyzja 96 z 10.11; kolumna Projekt planu, inaczej projekt karty; klasa
ticketu z planu, Od razu — `standard`), domyślnie `free`. Nie jest to nadrzędna reguła Tide. Tę samą politykę
stosują: sprawdzanie planu (`plans.read`, także domyślny recenzent pustej kolumny Review — przy `other-family`
wykonawca innej rodziny), edycja ticketu w oknie (`EditCardPlan edit`), start Od razu (`StartCard`; przy `free`
bez innego wykonawcy recenzentem może być sam autor, przy `other-family` recenzent innego dostawcy jest dozwolony
także przy wybranym dostawcy zlecenia), start ticketu z planu, wybór i zmiana recenzenta planu oraz cel eskalacji
poprawek. Reguła `ESCALATE_FIX_ROUND` się nie zmienia; przy `other-family` cel eskalacji nie należy do rodziny
recenzenta ocenianego ani następnego review (rodzina z dziennika kroku, przydziału albo katalogu).
Gdy wybrany recenzent nie spełnia polityki, bramka szuka wykonawcy klasy ticketu lub mocniejszej,
z zachowaniem lokalnego/chmurowego zakresu. Przy braku kandydata zapisuje idempotentne `review.question`
z `reason: independence`, `policy`, `ticket`, `round` oraz `step.waiting` z `cause: review-independence`.
Pytanie podaje powód (wymóg polityki, odrzucony recenzent, brak zgodnego wykonawcy) i trzy wyjścia: wybierz
innego recenzenta, poczekaj albo poluzuj politykę projektu. Poluzowanie działa w następnym przebiegu bez restartu.
Panel pokazuje pytanie i wybór recenzenta przez istniejące `ResolveCardStep reassign-reviewer`.
Pojawienie się zgodnego recenzenta zamyka pytanie zdarzeniem `review.decision` z `choice: review-ready`;
start review usuwa je z projekcji, aby późniejsze pytanie po progu rund działało jak dotąd.

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

Zgodę „uruchamiaj zawsze dla ticketu” można cofnąć w pełnej karcie (Tablica i Sesje).
`/Board RevokeTicketConsent(id, {ticket, orderSeq, baseVersion})` przyjmuje kontekst z
`derived.flow.ticketConsents[]` i zwraca `{ok, ticket, active: false, seq}`. Powtórzenie zwraca ten sam wynik.
Obcy ticket daje `bad-ticket`, a stare zlecenie lub wersja bazy — `stale-context`.
Projekcja zawiera `active`, `revoked`, `canRevoke`, `seq`, `orderSeq` i `baseVersion`.
Źródłem jest ostatnie `outside.ticket-consent` lub `outside.ticket-consent-revoked` w bieżącym
zleceniu i wersji bazy; nowy start i korekta bazy zerują projekcję, zachowując historię.
Po cofnięciu automatyczne zgody i zapamiętane rodziny projektu nie uruchamiają kolejnych poleceń tego
ticketu. Reguły projektu i inne tickety pozostają ważne. Jednorazowe ręczne „Uruchom” działa tylko dla
swojego polecenia; nowe „Uruchamiaj dla ticketu” przywraca automatyzację. Cofnięcie unieważnia
oczekujące decyzje automatyczne i decyzję `run-ticket`; odrębna ręczna zgoda na polecenie pozostaje.
Kontrola bezpośrednio przed startem kolejki podlega aktualnym zakazom. Operacja cofnięcia i start
biegną w tym samym szeregowym wątku; polecenie, które już wystartowało, kończy się normalnie.

**Odmowa wygrywa z automatyczną zgodą (PT-311).** Odmowa człowieka (`deny` z panelu albo od koordynatora,
bez `auto`) jest ostateczna dla tego samego polecenia (porównanie bez różnic w białych znakach) w tym tickecie
bieżącego zlecenia i wersji bazy (`orderSeq`, `baseVersion`) — także gdy krok z odmową opiekun odesłał do
poprawy planu (to nie zmienia zlecenia ani bazy). Kolejna prośba outside o to polecenie nie dostaje
automatycznej zgody (zapamiętana rodzina, `outsideAuto`, zgoda ticketu) ani przy wyniku kroku, ani w ponownym
sprawdzeniu reguł; wstrzymana karta także nie dostaje nowych automatycznych zgód (po wznowieniu zgoda powstaje
według aktualnych reguł). Pominięcie zapisuje raz na krok i powód `outside.auto-skipped` z `reason`
`denied-before` albo `paused`. Automatyczna zgoda w kolejce, której polecenie człowiek odmówił później w innym
kroku ticketu, jest unieważniana przed startem (`outside.decision-invalidated` z `reason: denied-before`).
`ResolveCardStep` z `deny` na kroku z automatyczną zgodą przed `outside.started` zapisuje
`outside.decision-invalidated` (`reason: denied`) i odmowę, a proces nie startuje; po starcie zwraca
`already-started`, a polecenie działa dalej. Ręczna zgoda (`run`, `run-ticket`, `run-always`) pozostaje
zapisaną odpowiedzią (`already-decided`) i działa także po wcześniejszej odmowie. Kontrola decyzji tuż przed
startem, uruchomienie i zapis `outside.started` są pod tą samą blokadą dziennika co zapis odmowy.
Automatyczna odmowa Tide i odmowa opiekuna nie blokują późniejszych zgód.

**Kolejka poleceń poza piaskownicą i kolejka ciężkich poleceń (K-39 #3, PT-373).** Zgoda (ręczna albo
automatyczna: zapamiętana rodzina projektu, „dla ticketu”, `outsideAuto`) trafia do kolejki według czasu zgody.
Polecenia dzielą się na dwie klasy (`cardflow/heavyqueue.py`, czyste `classify` i `admit`):

- **ciężkie** — polecenie z rodziny `tests.changed` albo `tests.full` efektywnego kontraktu testów projektu
  (zatwierdzony kontrakt, inaczej cele `make` worktree: `make test-changed BASE={base}`, `make test`) oraz każda
  próba testów po scaleniu (K-88). Rodzina to argumenty polecenia kontraktu do pierwszego `{base}`, porównywane
  słowo po słowie z początkiem polecenia bez początkowych przypisań: `make test-changed BASE=<dowolny SHA> JOBS=4`
  jest ciężkie, `make lint`, `make test-cardflow`, `ss -ltnp` — nie. Projekt z innym kontraktem (np. `dotnet test`)
  ma za ciężkie swoje polecenia. Ponowienie samych padniętych modułów (`rerunOf`) liczy się jak pełny przebieg.
  także polecenie pasujące do wzorca `heavyCommands` (niżej);
- **lekkie** — pozostałe polecenia.

**Wzorce `heavyCommands` (PT-382).** Budowy, testy innych technologii i pomiary wpisuje właściciel w ustawieniach
przepływu (np. `bash daemon/tests/perf_gate_k98.sh *`); takie polecenie czeka w tej samej kolejce, z tym samym limitem.

- *Argumenty* `classify` bierze przez `outside_argv(command, roots)` z `roots` = (worktree ticketu, repozytorium
  projektu, katalog kroku), tak jak zgoda przy starcie; bez `roots` `bash`/`sh` i `env` nie dałyby argumentów.
- *Dopasowanie* jest jedną funkcją `heavyqueue.pattern_matches` (użyje jej też `quietCommands`, #6), a nie
  `outside_matches`. Reguła ma format zgód „zawsze w projekcie”: dokładne polecenie albo rodzina z końcowym ` *`,
  a rodzina działa prefiksowo po argumentach. Reguła jest parsowana jak polecenie (z `roots`, z normalizacją).
- *Normalizacja* (wyłącznie klasyfikacja; polecenie uruchamia się bez zmian, a `outside_allowed`/`outside_risky`
  działają jak dotąd): początkowe przypisania i `env NAZWA=…` są pomijane, interpreter podany ścieżką
  (`/usr/bin/python3`, `.venv/bin/python`, `python3.12`) to `python3`. `env PYTHONPATH=… .venv/bin/python -m pytest …`
  pasuje więc do `python3 -m pytest *`, a `JOBS=4 make test-changed BASE=…` do rodziny kontraktu. Dotyczy też rodzin
  kontraktu.
- *Skrypty*: dla `bash`/`sh <skrypt> …` ze skryptem w `roots` (`script_in_roots`) ścieżka w poleceniu i we wzorcu jest
  sprowadzana do postaci względnej wobec worktree albo repozytorium (`./`, ścieżka bezwzględna w worktree i `sh` zamiast
  `bash` dają to samo). Skrypt spoza `roots` (także `../`) nie pasuje do żadnego wzorca.
- *Lista domyślna* (`heavyqueue.DEFAULT_PATTERNS`, rodziny z końcowym ` *`): `make test`, `make test-changed`,
  `make test-cardflow`, `make test-daemon`, `make test-board`, `make mist-golden`, `npm test`, `npm run build`,
  `npm run e2e`, `npx playwright test`, `dotnet test`, `dotnet build`, `go test`, `cargo test`, `pytest`,
  `python3 -m pytest`, `python3 -m unittest`, `python3 tools/test_runner.py`, `docker build`, `docker run`,
  `docker compose up`, `docker compose build`. `make test*` nie jest składnią dopasowania (`make test *` dopasowuje
  cel `test`, nie `test-cardflow`), dlatego cele są wyliczone.

Ciężkich biegnie naraz najwyżej `heavyLimit` na maszynę (ustawienie przepływu, 1..6, domyślnie 2), niezależnie od
projektu, karty i rodzaju zgody. Lekkie i ciężkie razem podlegają dotychczasowym limitom: `OUTSIDE_PER_PROJECT = 6`
w projekcie i `OUTSIDE_TOTAL = 7` łącznie (łączny nie jest mniejszy niż największy `heavyLimit`), więc lekkie
polecenie rusza mimo zajętych miejsc ciężkich, a starsza zgoda ciężka, która czeka na miejsce ciężkich, nie trzyma
w kolejce lekkich. Limit działa przy każdej decyzji startu: zmniejszenie `heavyLimit` nie przerywa trwających,
a nowe ruszają dopiero, gdy trwających jest mniej niż nowy limit. Kolejność wśród ciężkich to czas zgody.

Zbiór trwających to żywy `_outside_active` silnika (po sprawdzeniu kodów wyjścia i po każdym starcie w tym samym
ticku, także próby po scaleniu), a nie migawka stanu kart z fazy odczytu ticku: dwie karty sprawdzane w jednym ticku
nie ruszą obie przy jednym wolnym miejscu. Sam start zostaje pod blokadą dziennika z kontrolą decyzji tuż przed
`Popen` (PT-311). Wstrzymana karta nie trzyma miejsca w kolejce; po wznowieniu zgoda wraca do niej. Po restarcie
usługi trwające polecenia (także procesy wstrzymane z jakimkolwiek startem) zajmują miejsca, z klasą liczoną
z dziennika, a czekające wracają do kolejki z czasem zgody; wpis kolejki ma pole `heavy`.

Ticket, którego ciężkie polecenie czeka na miejsce, ma w pełnej karcie (`tickets[].statusLabel`) i na kafelku napis
`w kolejce: <polecenie> (ciężkie polecenia: <zajęte> z <limit> zajęte)`, np. „w kolejce: make test-changed
(ciężkie polecenia: 2 z 2 zajęte)” — pochodna żywej kolejki silnika, dokładana przy budowie stanu karty. Lekkie
polecenie zachowuje napis `w kolejce: <polecenie>`. Dziennik usługi zapisuje powód czekania raz na zmianę
(„ciężkie polecenia: 2 z 2 zajęte (…)”).

**Polecenia wymagające ciszy (K-39 #6, PT-376).** Pomiar wydajności zgłoszony przez krok jako wymagający ciszy rusza
sam dopiero na spokojnej maszynie, a w trakcie nie startuje nic ciężkiego (wcześniej koordynator ręcznie wstrzymywał
karty przez `SetCardPaused`).

- *Znacznik*: wynik kroku `outside` albo `denied` z poleceniem może mieć `"exclusive": true` (standard planu 1.5,
  „Kontrakt kroku”); pole trafia do `step.finished` jako `exclusive` (każdy wynik `denied`; zły typ — `false`, nie błąd
  kroku). Kartka kroku przy kontrakcie `outside` każe je dopisać do każdego pomiaru wydajności i innego polecenia,
  którego wynik zależy od obciążenia maszyny.
- *Wzorce* `quietCommands` (ustawienie przepływu, domyślnie pusta lista): polecenie pasujące do wzorca wymaga ciszy
  także bez znacznika. Dopasowanie to ta sama funkcja co `heavyCommands` (`heavyqueue.pattern_matches`, argumenty
  z `roots` i normalizacją), więc `bash daemon/tests/perf_gate_k98.sh *` rozpoznaje skrypt pomiaru w worktree.
  Polecenie wymagające ciszy jest zawsze ciężkie. Próba testów po scaleniu nie wymaga ciszy.
- *Warunek ciszy* (`heavyqueue.quiet`): brak trwających ciężkich i 1-minutowe obciążenie (`loadavg1`) mniejsze niż
  liczba rdzeni / 2. Odczyt jest wstrzykiwany (`heavyqueue.read_load`: `/proc/loadavg`, `os.cpu_count()`); błąd
  odczytu oznacza warunek niespełniony z powodem — nigdy start.
- *Start* (`heavyqueue.admit`): polecenie wymagające ciszy rusza tylko przy spełnionym warunku; gdy trwa (albo
  właśnie rusza), żadne inne ciężkie nie startuje; gdy czeka na czele kolejki ciężkich (pierwszy wpis w kolejności,
  który nie ruszył), nowsze ciężkie też nie startują (opróżnianie), żeby nie czekało w nieskończoność. Wpis wymagający
  ciszy dalej w kolejce niczego nie wstrzymuje. Lekkie polecenia i kroki agentów nie są wstrzymywane: nie przechodzą
  przez `admit`, a starsza zgoda ciężka wstrzymana ciszą nie trzyma w kolejce lekkich ani innych projektów.
- *Powody* (zdania usługi): „czeka na ciszę: obciążenie 7,1 przy progu 6,0”, „czeka na ciszę: biegnie make test
  (K-36)”, „czeka na ciszę: nie da się odczytać obciążenia maszyny (…)”, „wstrzymane: na czele kolejki czeka polecenie
  wymagające ciszy (K-36)”, „wstrzymane: biegnie polecenie wymagające ciszy: <polecenie> (K-36)”. Ticket dostaje
  zdanie jako `statusLabel` z `cause: quiet`, kafelek — w nawiasie po napisie kolejki, a migawka — w
  `queue.heavy.waiting[].reason`. `queue.heavy.load` podaje `avg1`, `cores`, `threshold` i `error`, a wpisy
  `running`/`waiting` mają `exclusive`.
- *Restart*: znacznik ciszy odtwarza się z `step.finished.exclusive` i bieżących `quietCommands` (dla trwających
  i czekających); klasa jest pamiętana do końca uruchomienia polecenia, więc zmiana wzorców dotyczy nowych zgłoszeń.

**Stan kolejki w migawce** (K-39 #4, PT-374, `cardflow/queueview.py`). Pole najwyższego poziomu `queue` migawki
tablicy (kształt w tabeli migawki) liczy silnik **raz na tick** z obiektów `FlowState` tej samej fazy odczytu
(`journal.state`), a `snapshot_json` tylko je dołącza — bez odczytów dziennika i plików kroków w ścieżce migawki.
Trwające: `outside_started` (polecenie i `since` z `outside.started`, ticket z `steps` albo `merge_check_steps`;
próba testów po scaleniu to pseudokrok `merge-check-*` z `kind: merge-check`), czekające: `outside_queued` i próby
w etapie `queued`, z poleceniem i czasem zgody ze stanu karty; kolejność i powód z `heavyqueue.admit`. Klasa polecenia
to ta sama klasyfikacja co przy starcie (`_outside_heavy_of`). Decyzja zgody na polecenie poza piaskownicą ma
w części technicznej `queue: "heavy" | "outside"`. Okno decyzji (`decision.js`) liczy z tego tylko arytmetykę
prognozy „Ruszy od razu: n, poczeka w kolejce: m” (ciężkie: `limit` minus trwające i czekające; lekkie: reszta limitu
projektu i łącznego) i nie zna limitów ani napisów usługi; bez pola `queue` pokazuje samą liczbę ticketów.

Decyzje przekazuje metoda D-Bus `/Board ResolveCardStep(id, request_json)` z `choice` równym `answer`, `allow`,
`deny`, `retry` lub `change-executor`; odpowiednio podaje się `answer` albo `executor`, a przy łańcuchu `allow`
(z `choice=allow`) albo `instead` (z `choice=deny`). Ponowione kroki dostają
osobne katalogi `.resume-<step>.step` i pliki wyniku; dziennik pozwala odtworzyć decyzję po restarcie usługi.

```bash
qdbus6 org.tide.Daemon /Board org.tide.Daemon.Board.ResolveCardStep K-12 '{"choice": "answer", "answer": "ISO 8601"}'
```

### Uwagi do najbliższego wznowienia kroku (PT-245)

`/Board AddCardStepNote(s id, s request) → s` przyjmuje obiekt JSON:
`{step, orderSeq, requestId, text}`. `step` to konkretny bieżący krok, `orderSeq` pochodzi z
`derived.flow.orderSeq` (numer zdarzenia `card.started`), a `requestId` to niepusty klucz klienta do
200 znaków. Treść musi być niepusta po usunięciu białych znaków i mieć najwyżej 16 KiB UTF-8.
Odpowiedź `{ok: true, id, noteId}` potwierdza trwały zapis `step.note-added` w dzienniku, bez
zatrzymywania procesu ani uruchamiania sesji. Ta sama treść, krok i klucz w tym zleceniu zwracają
ten sam `noteId` (również po doręczeniu); inna treść lub krok daje `note-conflict`. Nieaktualne
zlecenie daje `stale-order`, krok zakończony albo zastąpiony — `stale-step`, błędna treść — `bad-note`.
Zapis i sprawdzenie idempotencji są pod jedną blokadą dziennika.

Wspólne przygotowanie wznowienia dołącza oczekujące uwagi w kolejności zapisu. Dotyczy odpowiedzi,
retry, zmiany lub przekazania wykonawcy, przejęcia w Konsoli oraz automatycznego wznowienia po
poleceniu poza piaskownicą. Następca wskazuje `deliveredNoteIds` w `step.started`; intencja
`step-start` przechowuje tę samą listę do odtworzenia po awarii. Nieudany start bez trwałego
następcy pozostawia kolejkę. Przy przejęciu istniejącego kroku listę zapisuje `step.entered`.
Gdy nieprzejęte zadanie Konsoli zostanie atomowo anulowane, `step.console_closed.cancelledNoteIds`
cofa potwierdzenie wyłącznie uwag dołączonych do tej próby (`consoleNoteIds` w stanie kroku).
Próba Konsoli ma pusty plik rozstrzygnięcia w trwałym katalogu wejścia kroku, pod nazwą
złożoną z `processFile` i unikalnego `consoleUnit`. Launcher i anulowanie zmieniają atomowo
jego nazwę odpowiednio na `.claimed` lub `.cancelled`; wygrywa tylko jedna operacja.
Plik i zmiana katalogu są synchronizowane na dysk przed potwierdzeniem. Trwałe `.cancelled`
pozwala odtworzyć cofnięcie po awarii przed zapisem zdarzenia także po utracie runtime lub
sprzątnięciu go przez `TaskFiles.sweep()`. Plik runtime nadal służy do przekazania polecenia;
spóźniony launcher nie uruchomi zadania po trwałym anulowaniu. Starsze zadania bez tego
śladu zachowują obsługę znacznika runtime `.cancelled`. Nieudane anulowanie lub samo
zamknięcie Konsoli nie cofa doręczenia.
Potwierdzenie znaczy dołączenie do polecenia, nie przeczytanie przez model. Kolejne wznowienie
nie powtarza bloku już przekazanych uwag. Sesja może nadal pamiętać wcześniejsze instrukcje.

Kolejka jest ograniczona do łańcucha `parentStep` tego samego ticketu i zlecenia. Niezależne
review, inny ticket i nowe zlecenie nie otrzymują uwag. Uwaga nie rozszerza zgód na polecenia,
nie zatwierdza planu i nie wymusza wznowienia po końcu pracy.

Pełna karta w Tablicy i Sesjach pokazuje formularz przy konkretnym kroku oraz
`flow.stepNotes[] = {noteId, step, text, orderSeq, requestId, status, statusLabel, successor, …}`.
Stan to `pending` („oczekuje”), `delivered` („przekazana — <następca>”) albo `undelivered`
(„nieprzekazana — krok zakończony”). `flow.steps[].canAddNote` określa dostępność formularza.
Błąd/pytanie/zgoda nadal mogą czekać na wznowienie; zakończenie bez wznowienia zachowuje widoczną
uwagę jako nieprzekazaną. Formularz zachowuje treść i klucz ponowienia po błędzie zapisu.
Odpowiedź zapisu rozlicza szkic po `requestId` także po otwarciu innej karty; powrót do pierwszej
pozwala wysłać następną uwagę po sukcesie albo ponowić zapis z tym samym kluczem po błędzie.

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
  `plan-review`, `plan-approval`, `tickets`, `closing` (Domknięcie), `main-merge` (Scalenie; po sukcesie pełna nazwa zapisanego celu). `state`:
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

### Wykrywanie stosu i szablony stosów (PT-193, pudełko karty)

`tasks/stack.py` → `detect(worktree)` zwraca `StackReport`: same fakty, bez decyzji. Moduł wyłącznie czyta pliki
(nie uruchamia `npm`, `dotnet` ani `make`, nie używa sieci) i jest deterministyczny: katalogi przeglądane
alfabetycznie (bez `node_modules`, `bin`, `obj`, `.git`…), listy bez powtórzeń w stałej kolejności. Raport:

- `stacks` — nazwy szablonów w stałej kolejności `node`, `playwright`, `dotnet`, `docker`, `python-qt`; `[]`, gdy
  projekt nie ma wzorca;
- `node` (`package.json`, `.nvmrc`/`engines`, `angular.json`, `karma.conf.js`, `playwright.config.*` ze zmiennymi `*PORT`),
  `dotnet` (`*.sln`, `*.csproj`: TFM, `Directory.Packages.props`: Testcontainers i `Microsoft.Playwright`, `global.json`),
  `python` (`Makefile`, `mk/*.mk`, `pyproject.toml`, `requirements.txt`: cele `test`/`lint`, Qt offscreen),
  `docker` (`docker-compose*.yml`), `testcontainers_images` (literały `"<obraz>:<tag>"` w `*.cs`/`*.ts` plików
  używających Testcontainers), `ci` (`.gitlab-ci.yml`, `.github/workflows`: obrazy, polecenia; deploy z `rsync`/`ssh`
  jest ignorowany), `nuget_sources` i `internal_addresses` (rejestry z `NuGet.Config`/`nuget*.config`; adres
  wewnętrzny = prywatny IP, nazwa bez kropki albo sufiks `.local`/`.lan`/`.internal`/`.corp`/`.biz`).

`propose_profile(report, project)` buduje z raportu słownik w kształcie `tide-box: 1` (walidację schematem robi
konsument, nie ten moduł) oraz listę `items` (pozycje do pakietu zgód: `kind`, `value`, `risk`, `enabled`, `description`).
Niezmienniki: nigdy `network.full`; pozycja czerwona nigdy nie jest włączona; adres wewnętrzny z `NuGet.Config` to
wyłączona pozycja czerwona z opisem (nie trafia do `network.domains`); obraz Testcontainers/compose trafia do
`docker.images`; `stacks: []` daje propozycję pustą z polem `question` (pytanie do okna planu, nie do kroków).

**Szablony stosów** to dane YAML w `tide_daemon/box/stacks/<stos>.yaml` (`base`, `node`, `playwright`, `dotnet`,
`docker`, `python-qt`), nie kod powłoki. Każdy ma `domains` (`name`, `risk`: `green`/`yellow`/`red`, `note`, opcjonalnie
`registry: true` dla rejestrów paczek), `apt` i `images` (szablon `docker` niesie Ryuk z przypiętą wersją i opisem
ryzyka K1 w `image_notes`). `domains` szablonów nie zawierają `github.com`, `gitlab.*` ani adresów wewnętrznych;
wyjątkiem jest lista `build-domains` (PT-192) — domeny tylko dla skryptu szablonu w czasie budowy (`docker` →
`github.com` dla wydania runc), nigdy dla kroków. Projekt nie używa biblioteki YAML, więc `box/templates.py` czyta
podzbiór (skalary, listy, listy map). Funkcje:

- `work_domains(profile)` — suma `domains` szablonu `base` + szablonów z `stacks` + `network.domains`, bez powtórzeń
  (polityka kroków w proxy); nieznany stos → `ValueError`;
- `build_domains(profile)` — `work_domains` + `build-domains` szablonów (lista budowy pudełka dla proxy). Podobnie
  `build_apt(profile)` i `build_images(profile)`;
- `known_domains()` — słownik domena → klasa ryzyka (przy rozbieżności wygrywa groźniejsza), dla `classify`;
- `registry_domains(stack)` — domeny z `registry: true`; z nich składa się `tasks/node.REGISTRY_DOMAINS` i
  `tasks/dotnet.REGISTRY_DOMAINS` (wartości jak przed zmianą).
## Scenariusz 03.11 — ocena na prawdziwym modelu

PT-156 rozdziela dwie rzeczy: test okablowania przez `FlowEngine` na atrapach
(`daemon/tests/test_cardflow_caretaker_0311.py`) oraz ręczną ocenę diagnozy prawdziwego
wykonawcy. Atrapa dostaje gotową odpowiedź; jej sukces nie mierzy jakości modelu ani „95%”.
Test sprawdza dziennik, `engine.derived()`, katalog `in/context/`, strażnik oraz skutki akcji.
W tym zestawie granica gita przy zapisie planu i przygotowaniu review jest atrapą; testy
piaskownicy gita pozostają w osobnym zestawie. Nie oznacza to testu izolacji systemowej.

Materiały są w `tests/fixtures/caretaker-0311/`: podkatalogi `1`, `2`, `3`, `4a`, `4b`
zawierają odtworzony dziennik karty, wycinek kroku i kryterium oceny. Są rekonstrukcją
przyczyn z kartki K-37, a nie kopiami prywatnych dzienników. Ścieżki są umowne,
sekrety zamaskowane. Nie wgrywaj ich do działającej Tablicy.

Procedura ręczna, poza `make test`:

1. Dla każdego z pięciu podkatalogów otwórz świeżą sesję wybranego prawdziwego
   wykonawcy w Konsoli, z repozytorium tylko do odczytu. Zanotuj nazwę wykonawcy,
   model, ustawienia rozumowania, datę i identyfikator sesji. Nie dawaj mu `expected.md`.
2. Przekaż `journal.jsonl`, `step.log`, `docs/agent-plan-standard.md` i
   `docs/sandbox-and-permissions.md`. W przypadku 2 dołącz odrzucony
   `2/plan.md` i kartki `2/tickets/1.md`, `2/tickets/2.md` z katalogu materiałów;
   poprzednia propozycja i błędy są w dzienniku. Nagłówek „Krok” w wycinkach
   przypadków 1, 2 i 4a wskazuje konkretne zdarzenie `step.started`.
   W przypadku 3 zatwierdzona baza i decyzja A są w dzienniku. Dla 4b oceń oba
   przebiegi, a nie tylko ostatnią linię błędu.
3. Użyj zadania: „Jesteś opiekunem karty Tide. Na podstawie dołączonych źródeł
   zdiagnozuj przyczynę i zaproponuj jedną akcję z kontraktu opiekuna. Nie wykonuj
   akcji, poleceń ani zapisów w repozytorium lub Tablicy. Zwróć JSON jak wynik
   kroku: result=done, action oraz wymagane pola. Dozwolone są retry (step,
   instruction, reason), return-plan (feedback, reason), retry-stage lub escalate
   (summary, recommendation); opcjonalny report zawiera description. Nie możesz
   zatwierdzać planu, wydawać zgody, scalać ani odrzucać ticketu. Ścieżki w materiale
   są umowne. Po JSON wyjaśnij diagnozę, wskazując konkretne zdarzenia i linie.”
4. Zapisz surową odpowiedź poza repozytorium przed otwarciem `expected.md` danego
   przypadku. Porównaj osobno: trafność przyczyny, oparcie jej na źródłach,
   kompletność parametrów akcji i zachowanie granic decyzji użytkownika. Błędne
   wyjaśnienie przy trafnej nazwie akcji nie zalicza diagnozy.
5. W notce karty K-37 zapisz dla każdego przypadku: wykonawcę/model, sesję, wynik
   JSON, ocenę każdego kryterium (tak/nie z uzasadnieniem), cytowany zamaskowany
   dowód oraz ewentualną lukę Tide. Zachowaj odpowiedź modelu; nie dopisuj jej do
   testowego expected.md. Kolejne oceny rób w nowych sesjach, bez poprzednich odpowiedzi.

Ocena nie jest bramką testów i nie uruchamia się automatycznie. Testy nie wywołują
prawdziwych modeli. Doprecyzowanie koordynatora z 06.11 zachowuje kontrakt PT-143:
zły wynik lub niedozwolona akcja oddają natychmiast dokładnie jedną decyzję
z rekomendacją i streszczeniem jednej próby (`test_bad_*`). Limit dotyczy błędów
wykonania poprawnej akcji (`test_failed_action_limit_*`), np. wskazania obcego kroku
w retry albo utrzymującego się błędu etapu. Każdy z pięciu przypadków ma oba warianty;
nie ma testów oznaczonych jako oczekiwane porażki. Gdy błędny wynik nie niesie
rekomendacji modelu, Tide dodaje techniczną wskazówkę sprawdzenia błędu i wyboru
dalszego działania. Nie jest ona diagnozą przypisywaną modelowi.

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
działanie → `caretaker.escalated`. Akcje inne niż oddanie decyzji kończą się `caretaker.action` (`problem, step,
target, ticket, action, params, ok, error, code, label, retried, stale, detail?`) po parze „zamiar → wynik”
`caretaker-action` — „Ponowienie, limit i nieaktualne akcje” niżej (PT-150).

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
  działania od ostatniego zamknięcia poza samym oddaniem decyzji: `{step, action, ok, reason, text}`, bez
  nieaktualnych); możliwość z `optionId` jest polecona
  i pierwsza, żadna nie jest wybrana (D10). Renderowanie w oknie decyzji to #15. Eskalacja powiadamia raz
  (zwykłe `step.notice` kroku problemu).
- Historia (`flow.history[]`, rodzaj `caretaker`, `actor: "caretaker"`): „Opiekun: oddaje decyzję — <streszczenie>”
  i „Opiekun: nie udało się wykonać działania „<akcja>” — <błąd>”; wynik odrzucony przez strażnika (PT-158):
  „Opiekun: wynik odrzucony, akcja nie wykonana — <powód>”; PT-150: „Opiekun: ponowił <krok> z instrukcją —
  <powód>” (bez osobnego wpisu „Decyzja: ponowienie kroku” — `step.decision` z polem `caretaker` nie ma wpisu),
  „Opiekun: nie udało się (ponowienie kroku) — not-problem-step: …”, „Opiekun: działanie „<akcja>” nieaktualne, nic
  nie wykonano — <powód>” i „Opiekun: oddaje decyzję po N działaniach (limit) — <streszczenie prób>”.

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
Akcja opiekuna przerwana między zamiarem a wynikiem `caretaker-action` — „Ponowienie, limit i nieaktualne akcje”.

**Kiedy opiekuna nie ma** (dzisiejsza decyzja bez kroku opiekuna): karta wstrzymana, w Backlogu, Gotowych albo
Archiwum, błąd `login`, limit dostawcy, wina `user`, opiekun wyłączony w ustawieniach (`caretakerEnabled: false`,
bez dopisku) i karta „Tylko lokalnie” (`preferredProvider: local`) bez wykonawcy lokalnego w katalogu (decyzja
z dopiskiem „opiekun niedostępny dla pracy tylko lokalnej”); w obu ostatnich `flow.caretaker.enabled: false`
(„Ustawienia” niżej, PT-151).

### Wyzwalacze

`TRIGGERS` to lista funkcji `(st) -> [kandydat]`, kandydat `{trigger, step?, ticket?, stage?, cause?, decision?}`.
Do startu opiekuna wystarcza wpis w rejestrze, także dla problemu bez kroku (`flow-error:<etap>`, `stalled:card`):
`caretaker.started` ma wtedy pusty `target`, a krok opiekuna pracuje w worktree ticketu (gdy kandydat ma `ticket`)
albo w katalogu roboczym karty (`card-branch`); aktualność problemu przed startem sprawdza zawsze `problems()`.

#### Błąd kroku ticketu (`error`, PT-143)

Ostatni krok ticketu (`ticket`, `fix`, `review`, `conflict`) zakończony `error` po automatycznym ponowieniu
(`attempt ≥ 1`) i ewentualnym przejściu na mocniejszego wykonawcę, bez decyzji, bez kroku-następcy, bez
odrzucenia ticketu; bez `login`, limitu dostawcy i winy `user`. Id: `error:<korzeń łańcucha>`.

#### Ten sam powód i brak postępu (`repeat`, `no-progress`, PT-148)

Dwa wpisy `TRIGGERS` (`_repeat_trigger`, `_noprogress_trigger`) po `error`. Kolejność względem automatów silnika:

1. **Pierwsza porażka** — bez zmian: automatyczne ponowienie (`attempt: 0` → `step.retry` → próba 1), potem
   (próba 1) przejście na mocniejszego wykonawcę, potem `error` opiekuna. Wyzwalacz zastępuje wyłącznie kolejną
   *ślepą* próbę z tą samą przyczyną; przejście na mocniejszego wykonawcę, limity dostawcy (D3) i `login` zostają.
2. **`repeat` (wyzwalacz 2)** — dwa kolejne `step.finished` w tym samym tickecie (albo tym samym rodzaju kroku
   planu, gdy krok nie ma ticketu) z tym samym `result` i tą samą przyczyną: `faultCause`/`reason` przez
   `faults.of_finished` (swobodny powód agenta liczy się jako ta sama przyczyna tylko przy identycznym tekście,
   `other-<skrót>`), a dla `denied` — to samo polecenie po automatycznej odmowie łańcucha (`_auto_deny`) albo po
   `step.retry` opiekuna tego problemu. Kolejne odmowy po jego instrukcjach pozostają w tym samym limicie. Nie
   dotyczy `login`, limitu dostawcy i winy `user`. Kandydat nie ma decyzji (`decision` puste), więc `retry` idzie
   ścieżką bez decyzji (`step.retry` z `caretaker` i `instruction`), a `stale` — regułą `watermark`.
   Id: `problem_id("repeat", …)` → `repeat:<ticket>:<przyczyna>`, dla kroków planu `repeat:<rodzaj kroku>:<przyczyna>`
   (bez `seq`); licznik działań to `owned()`. Gdy łańcuch ma już problem (np. `error:`), kandydat dołącza do niego
   (`triggers[]`, id znanego problemu) — ponowienie opiekuna i kolejne błędy z tą samą przyczyną liczą się do jednego
   limitu `caretakerActionLimit`.
3. **Zamiast ślepej próby** — `FlowEngine._caretaker_takes_over` (`caretaker.takes_over`) jest pytane w trzech
   miejscach automatycznego ponowienia próby zerowej (`_advance`, `_advance_planning`, `_plan_step_decision`)
   i w `_auto_deny`: dla kroku, który jest drugim z tą samą przyczyną, `step.retry` ani automatyczna odmowa nie
   powstają — krok czeka na opiekuna (`_caretake` na końcu przebiegu). Druga automatyczna odmowa łańcucha z tym samym
   poleceniem w tickecie uruchamia więc opiekuna przed `AUTO_DENY_LIMIT`. Bez opiekuna (karta wstrzymana, Backlog,
   „Tylko lokalnie”) zostaje dzisiejszy automat.
4. **Po oddaniu** — gdy opiekun oddał problem (`escalate` albo limit), automatyczne ponowienie nie wraca: błąd
   kroku z próby zerowej staje się zwykłą decyzją użytkownika (`caretaker.handed_repeat` w `decisions`, `state`,
   `resolve_step` i powiadomieniach), z `caretaker{…}` jak przy `error`. Decyzja użytkownika zamyka problem (licznik
   od zera, to samo id).
5. **`no-progress` (wyzwalacz 6)** — krok zatrzymany przez strażnika (`reason: no-progress`, bez zmiany
   `stepGuardMinutes` i `stepstop`) po automatycznym ponowieniu, także dla `planning` i `plan-review`.
   Kontekst dostaje też powtórzony zastój próby zerowej przejętej przez `repeat`; pierwsze automatyczne
   ponowienie zostaje. Dołącza do problemu tego łańcucha i dokłada do kartki sekcję „Krok bez postępu”: czas zastoju (od
   `stalledSince` do zatrzymania) i końcówkę zdarzeń sesji (ostatnie 5 z `messages` wyniku kroku, po 300 znaków).
   Wiadomości sesji i odmówione polecenie są maskowane przed normalizacją i skracaniem.
   Dziennik kroku po ludzku jest w plikach kontekstu.

Kartka (`BRIEF_SECTIONS`, `BRIEF_DETAILS`): problem `repeat` ma opis błędu kroku i sekcję „Ten sam powód drugi raz”
(oba kroki, przyczyna, powody albo odmówione polecenie, ostrzeżenie przed ślepą powtórką); przy problemie otwartym
przez `error` sekcje dołączonych wyzwalaczy są dopisane za opisem błędu. Wszystko przechodzi przez `faults.mask`.

#### Błąd Tide i karta stoi (`flow-error`, `stalled`, PT-147)

Dwa wyzwalacze bez kroku-sprawcy i bez decyzji (`caretaker.started` z pustym `target` i `decision`):

- **`flow-error`** (wyzwalacz 4) — błąd Tide (`flow.error`), który się utrzymał. Silnik zapisuje błędy jednego
  przebiegu przez `FlowEngine._flow_error`: `flow.error` raz dla tego samego etapu i tekstu, z polami `stage`, `step`,
  `ticket` i `at` (zegar silnika); ten sam tekst w kolejnym przebiegu co najmniej 10 s po pierwszym
  (`FLOW_ERROR_PERSIST`) dopisuje jeden `flow.error-persisted` (`error`, `stage`, `since` — `seq` pierwszego).
  Problemem jest też błąd powtórzony z tym samym tekstem po zniknięciu (nowy `flow.error` z tekstem, który już był od
  `card.started` w tym samym etapie). Obserwacje, utrzymanie i powtórzenia są liczone osobno dla każdego etapu:
  równoległe błędy A/B są dwoma problemami, także przy identycznym tekście; eskalacja B nie ukrywa A.
  Jednorazowy błąd, który w następnym przebiegu nie wystąpił, problemem nie jest. Etap (`stage`):
  `advance:<ticket>` — dalszy etap ticketu po jego ostatnim kroku, `plan-next` — start kolejnego ticketu planu,
  `advance` — pierwszy krok, `close` — domknięcie karty, `plan-commit` — commit planu. Id:
  `flow-error:<etap>` (np. `flow-error:advance:PT-7`); opiekun pracuje w worktree ticketu z `flow.error`, a bez
  ticketu — w katalogu karty. Błąd znika, gdy rusza krok jego etapu (`step.started`) albo etap wykona się bez błędu
  w zwykłym przebiegu lub po ponowieniu (`flow.error-cleared` z `stage`; udany `result` akcji `plan-commit`
  zdejmuje błąd commita bez startu kroku). Rozwiązanie jednego etapu nie zdejmuje błędów innych ticketów.
  Pominięte domknięcie (np. trwa krok opiekuna) nie kasuje błędu `close`. Commit planu odbywa się raz na przebieg,
  przed startem kolejnych ticketów; jego awaria ma wyłącznie etap `plan-commit`, także po zakończonym planowaniu.
  Zwykły przebieg zapisuje również końcowy stan zakończonego planu.
- **`stalled`** (wyzwalacz 5) — karta stoi (`card.stalled`: 3 minuty bez kroku, akcji i nazwanego powodu czekania)
  i nadal nie ma nazwanego powodu (`guard_reason` liczony bez pracy opiekuna: jego kroku, czekania na slot i par
  „zamiar → wynik”). Limit, slot dostawcy, wstrzymanie, decyzja, plan do zatwierdzenia, trwający krok albo zaczęta
  akcja — nie ma problemu. Karta, która stoi na błędzie Tide będącym już problemem `flow-error`, nie dostaje
  drugiego problemu. Id: `stalled:card`, katalog karty.

Praca opiekuna nie jest pracą karty: `caretaker.*`, kroki, czekanie i pary „zamiar → wynik” opiekuna,
`flow.error-persisted` i `stage.retry` nie przesuwają `last_activity_ts` i nie zdejmują „stoi”
(`state._bookkeeping`; wyjątek to `caretaker.decision` użytkownika, który rozpoczyna nowy okres bezczynności).
Dopóki opiekun pracuje, karta ma znacznik „opiekun sprawdza kartę” (przy błędzie ticketu —
„opiekun sprawdza PT-…”) zamiast „stoi” albo tekstu błędu; po eskalacji (albo po limicie) wraca dzisiejszy znacznik
błędu albo zastoju, diagnoza jest w historii („Opiekun: oddaje decyzję — …”) i w `derived.flow.caretaker.escalated[]`,
a `flow.decisions[]` dostaje decyzję „opiekun” (PT-154). Gdy opiekun działa, powiadomienie o zastoju czeka do
eskalacji; bez dostępnego opiekuna zostaje przy `card.stalled`.

### Decyzja opiekun

Eskalacja problemu `flow-error:<etap>` lub `stalled:card`, także po limicie działań, daje rodzaj `caretaker`
w `decisions.KINDS` i `derived.flow.decisions[]` (PT-154). Niepuste `happened` i `why` opisują zatrzymanie
karty i potrzebę decyzji. Pole `caretaker{summary, recommendation, tried[], optionId, failed}` zasila wspólny
blok „Opiekun poleca” oraz zwijane „Co próbowano”. Okna Tablicy i Sesji pokazują „Ponów etap”, „Zamknij”
oraz „Porozmawiaj z opiekunem”; nie wybierają żadnej domyślnie. Powiadomienie o tej decyzji powstaje raz, z zapisem `flow.notice`
w dzienniku, także po restarcie usługi. Dotychczasowe decyzje kroków nadal korzystają ze swoich metod.

`ResolveCardCaretaker(s id, s request) → s json` na `/Board` (`org.tide.Daemon.Board`) przyjmuje obiekt JSON:
`{"choice":"retry-stage"}` albo `{"choice":"dismiss"}`. Opcjonalne `problem` wskazuje id problemu (okno przekazuje
je z możliwości decyzji); bez niego metoda wybiera pierwszą decyzję opiekuna. Nieznany wybór daje `bad-choice`,
brak oczekującej decyzji lub nieaktualny problem — `not-waiting`, niepoprawne pola lub format — `bad-request`.

Oba wybory zapisują `caretaker.decision` i zamykają problem. `retry-stage` dodatkowo ponawia etap jednym
przebiegiem `_advance_card` przez istniejącą ścieżkę `stage.retry`; `dismiss` nie wykonuje akcji. Następne
wystąpienie błędu lub zastoju liczy działania od zera. Zamknięcie zastoju rozpoczyna nowy okres bezczynności;
zamknięcie błędu usuwa jego bieżący znacznik, więc kolejne wystąpienie jest nową obserwacją etapu.

Pole `caretaker` jest wyłącznie wewnętrznym oznaczeniem akcji opiekuna: `ResolveCardStep` z tym polem
w żądaniu D-Bus zwraca `bad-request`. Sygnatury istniejących metod pozostają bez zmian.

### Rozmowa z opiekunem

`/Board TalkToCaretaker(s id, s request) → s json` przyjmuje `{}` albo `{"problem":"<id problemu>"}`
i zwraca `{ok: true, step}`. Pełna karta z dziennikiem, przed zakończeniem, daje przycisk „Porozmawiaj
z opiekunem” (`derived.flow.canTalkToCaretaker`). Ta sama możliwość w decyzji `caretaker` i w decyzjach
eskalowanych przekazuje id konkretnego problemu. Bez id rozmowa wybiera pierwszy otwarty problem;
gdy żadnego nie ma, służy omówieniu stanu karty, a akcje wymagające problemu są nieaktualne.

Tide otwiera nadzorowaną kartę Konsoli w kontekście worktree karty. Krok `caretaker-chat` („rozmowa
z opiekunem”) pozostaje osobno od kroków ticketów. Ma kartkę w trybie rozmowy, zamaskowane
`in/context/` (ten sam limit 400 kB), tę samą piaskownicę i oczyszczone środowisko co opiekun w tle.
Program działa w swoim `out/`, z zapisem wyłącznie tam; worktree i gałęzie są pod strażnikiem PT-158.
Obowiązuje wybór wykonawcy opiekuna i „Tylko lokalnie”. Brak Konsoli daje `no-terminal`, brak przepływu
lub zakończona karta — `no-step`, druga otwarta rozmowa tej karty — `console-open`.

Rozmowa zapisuje kolejne `out/action-1.json`, `action-2.json` itd., bez nadpisywania wcześniejszych
plików. Każdy plik stosuje kontrakt wyniku kroku opiekuna w tle (`result: done`, `action` i parametry).
Cały JSON należy zapisać jednym wywołaniem narzędzia zapisu. Niepełny JSON jest odrzucany dopiero, gdy
metadane pliku nie zmienią się między dwoma przebiegami; odczyt w trakcie zapisu nie zużywa nazwy akcji.
`_poll_interactive` odczytuje pliki w kolejności numerów, utrwala zwalidowany wynik wraz z kontekstem
w `caretaker.chat-action` i wykonuje przez wspólną ścieżkę PT-150. Klucz pary „zamiar → wynik” zawiera
id rozmowy i nazwę pliku. Restart między zamiarem, skutkiem i wynikiem nie wykonuje akcji drugi raz;
podmiana lub usunięcie obsłużonego pliku nie zmienia utrwalonego zlecenia.

Te same rejestry, zakazy i kontrola `stale` chronią każdą akcję. Próba `approve-plan`, zgody albo scalenia
jest odrzucona z powodem w historii. Zapis w worktree lub przesunięcie gałęzi kończy rozmowę
z `caretaker-wrote`; do potwierdzenia zatrzymania żadne kolejne pliki akcji nie są wykonywane.
Historia używa podpisu „Opiekun (rozmowa): …” i `mode: chat`. Rozmowa oraz jej akcje nie zwiększają
licznika działań problemu. Zamknięcie Konsoli kończy rozmowę; nie tworzy zwykłej decyzji o wznowieniu ticketu.

Kartka (sekcje `flow-error` i `stalled` w `BRIEF_SECTIONS`): tekst błędu, etap, krok i ticket, podpowiedź Tide dla
użytkownika (`flow_error_hint`) albo od kiedy karta stoi i ostatni krok, ostatnie 15 zdarzeń karty (bez pracy
opiekuna) i otwarte pary „zamiar → wynik”.

### Zgłoszenie błędu Tide

Wynik opiekuna może przy każdej akcji i każdym wyzwalaczu zawierać opcjonalne
`"report": {"description": "opis błędu Tide"}` (1–4000 znaków). Zgłoszenie zapisuje kartę w Backlogu
projektu `plasma-tide` albo dopisuje wystąpienie do otwartej, niescalonej karty o tym samym podpisie.
Karta zarchiwizowana lub scalona nie przyjmuje kolejnego wystąpienia. Wspólny kod z `ReportStepFault`
wylicza podpis, wybiera kartę i maskuje opis oraz ograniczony wycinek dziennika przez `faults.mask`.
Dla problemu bez kroku podpis ma postać `flow/tide/<przyczyna>`: przyczyną jest etap `flow.error`
(np. `flow/tide/advance`), a dla zastoju `stalled`. Tekst błędu pozostaje zamaskowany w treści.

Samo `report` nie ponawia kroku ani etapu (A7). Osobny wpis `caretaker.reported` daje historię
„Opiekun: zgłosił błąd Tide (K-<n>) — <powód>”. Brak projektu daje wpis niepowodzenia
`no-report-project`; akcja główna nadal się wykonuje. Wystąpienie zawiera trwały znacznik wyliczony
z karty źródłowej i kroku opiekuna, zapisany razem z treścią zgłoszenia. Powtórne zastosowanie zapisanego
wyniku nie dodaje zgłoszenia drugi raz, także po przerwaniu przed wpisem `caretaker.reported` oraz po
archiwizacji lub scaleniu karty zgłoszenia. Nowy krok opiekuna ma osobny identyfikator wystąpienia.

Decyzja `caretaker` udostępnia pole „Zgłoś błąd Tide”. `PreviewStepFault` z `problem`, `description`
i opcjonalnym `choice` pokazuje kartę bez zapisu. `ResolveCardCaretaker` z
`{"choice":"report","problem":"flow-error:advance","description":"…"}` zapisuje zgłoszenie i zamyka
problem bez ponawiania etapu. Pusty lub za długi opis daje `bad-request`. Dotychczasowe żądania
`PreviewStepFault` i `ReportStepFault` z `step` zachowują swoje działanie, w tym retry po ręcznym
`ReportStepFault`.

### Akcje

`ACTIONS` to `nazwa → Action(kinds, params, apply, description)`; `kinds` — wyzwalacze, przy których akcja jest
dozwolona (`"*"` — każdy), `params` — pola wyniku (`Param(required, minimum, maximum, hint, choices)`), `apply(port,
problem, params)` działa przez port silnika (`engine._CaretakerPort`). Walidacja jest w Tide (`parse_result`), nie
tylko w kartce: nieznana nazwa (`approve-plan`, `allow`, `merge`, `push`, `reject`, …) → `bad-caretaker-action`;
brak pliku albo zły format → `bad-caretaker-result`; `result` inny niż `done` → nieudane działanie (`question` niesie
treść pytania jako streszczenie, jak `escalate`; inne — `caretaker-result`); zatrzymany albo niewystartowany krok —
`caretaker-stopped`, `caretaker-not-started`; krok, który pisał poza `out` — `caretaker-wrote` (strażnik, przed
walidacją wyniku, „Piaskownica i strażnik”). Długości: instrukcja/odpowiedź/uwaga do 4000 znaków, powód 1–600,
streszczenie do 1200. Nieudany wynik kroku opiekuna (wszystkie kody powyżej) od razu oddaje problem użytkownikowi;
nieudane wykonanie poprawnej akcji (np. `not-problem-step`) jest działaniem liczonym do limitu, po którym rusza
kolejny krok opiekuna (PT-150, „Ponowienie, limit i nieaktualne akcje”).

#### `escalate` (PT-143)

Oddaje decyzję użytkownikowi: `summary` (wymagane, 1–1200 znaków), `recommendation` (wymagane, 1–600), `optionId`
(opcjonalne, id możliwości decyzji problemu). W rejestrze nie ma akcji zatwierdzenia planu, zgody, uruchomienia poza
piaskownicą, scalenia, push ani odrzucenia ticketu.

#### `retry` (PT-150)

Ponawia krok problemu z instrukcją: `step` (wymagane, id kroku z linii „Kroki do ponowienia” kartki), `instruction`
(wymagane, 1–4000 znaków), `reason` (wymagane, 1–600). Instrukcja trafia do kartki wznowionego kroku jako
„Instrukcja od opiekuna karty (… ma pierwszeństwo przed wcześniejszym planem kroku)” (`handoff.resume_context(…,
caretaker=True)`); opiekun wyprowadza ją z `step-log.md`, historii i dziennika z katalogu kontekstu. Dwie ścieżki:

- problem z decyzją (`decision` w `caretaker.started`, np. `error:`) — istniejące `resolve_step` `retry` z instrukcją
  jako `answer`; `step.decision` dostaje `auto: true` i `caretaker: <problem>` (parametr silnika
  `caretaker_problem`, niedostępny z D-Bus), a wznowienie robi `_advance` jak przy decyzji użytkownika,
- problem bez decyzji (np. `repeat:` z #6) — wewnętrzna ścieżka automatycznego ponowienia: `step.retry` z `caretaker:
  <problem>`, `caretakerStep` i `instruction`, bez `step.decision`; `_advance` wznawia krok z `parentStep` i
  `attempt: 1`, z instrukcją w kartce. Do wznowienia decyzja kroku jest ukryta, a `problems()` pomija ten krok.

Ponowić można tylko krok z `caretaker.problem_steps(problem, st)`: przy decyzji — krok tej decyzji, bez decyzji —
krok łańcucha problemu bez następcy, zakończony wynikiem innym niż `done`, bez decyzji i ponowienia. Inny krok
(także spoza karty) to nieudane działanie `not-problem-step` z powodem w `error`, liczone do limitu.

#### `retry-stage` (PT-147)

Ponawia etap karty od razu, raz; bez pól; dozwolona tylko przy `flow-error` i `stalled` (przy innych problemach
`bad-caretaker-action`). Idzie wspólną ścieżką akcji (para `caretaker-action`, `caretaker.action`, limit,
nieaktualność): `apply` → `_CaretakerPort.retry_stage` → `FlowEngine._retry_stage` zapisuje `stage.retry`
(`problem`, `stage`, `caretaker`, `caretakerStep`) i wykonuje jeden przebieg `_advance_card` — tę samą funkcję, którą
`_tick_card` przechodzi etapy w każdym przebiegu (ostatnie kroki ticketów, kolejny ticket planu, domknięcie karty,
commit planu), z własną parą akcji opiekuna wyłączoną z „zaczętych akcji karty”. Wynik:

- błąd Tide ponawianego etapu (przy `stalled`: dowolnego etapu) — nieudane działanie `stage-failed` z tekstem
  błędu (błąd zapisuje zwykły `_flow_error`; ten sam etap i tekst nie daje nowego zdarzenia),
- przebieg bez żadnego zdarzenia poza pracą opiekuna — nieudane działanie `stage-no-change`,
- udany przebieg — `caretaker.action` z `ok: true` i `label` etapu („Opiekun: ponowił etap „advance:PT-7””); przy
  `flow-error` rozwiązanie etapu zdejmuje jego błąd tak samo jak zwykły przebieg; błędy innych etapów pozostają,
- `stage.retry` tego kroku opiekuna już jest (restart między zamiarem a wynikiem) — `stage-interrupted` bez drugiego
  przebiegu: etap ponawia się dokładnie raz, a dalej i tak ponawia go zwykły przebieg.

Nieudane działania liczą się do limitu: problem `flow-error`/`stalled` ma id bez `seq`, więc kolejne wystąpienia tego
samego etapu to ten sam problem; po trzech działaniach `caretaker.escalated` z `limit: true` i kolejny opiekun nie
startuje. Nieaktualność — reguła bez decyzji (niżej): problem zniknął (błąd zniknął, karta ruszyła) albo po
`watermark` jest `step.started`, `step.retry` albo `step.decision` w tickecie problemu (bez ticketu — w karcie)
albo zdarzenie karty z listy. Rozwiązanie danego etapu po `watermark` (`flow.error-cleared` lub udany wynik
`plan-commit`) też unieważnia akcję, nawet jeśli błąd potem wrócił; wtedy `caretaker.action` ma
`ok: false, error: "stale"`, bez `stage.retry` i bez zwiększenia licznika działań.

#### `return-plan` (PT-146)

Odsyła plan planującemu z uwagą opiekuna; dozwolona przy wyzwalaczu `plan-fix` (patrz „Odesłanie planu”) oraz
problemie ticketu `error`, `repeat` albo `waiting` dla pytania (patrz „Luka w planie w trakcie realizacji”).
Pola: `feedback` (wymagane, 1–4000 znaków — wiersz planu, bloker i błąd standardu planu 1.2; prośbę „odrocz tę
uwagę do ticketu #n” opiekun przekazuje tu dosłownie, bez osobnej reguły rozpoznawania odroczeń), `findings`
(opcjonalne, numery uwag ostatniego review od 1, po przecinku) i `reason` (wymagane, 1–600, do historii).

### Odesłanie planu (PT-146)

**Wyzwalacz 3** (`caretaker._plan_fix_trigger`, id problemu `plan-fix:<korzeń łańcucha kroków poprawy>`):

- `plan.revision-failed` — krok `plan-amend` zakończył się, ale poprawiony plan nie przeszedł sprawdzenia formatu.
  Problem ma decyzję `plan-revision-failed:<seq>`, którą `build_decisions` ukrywa na czas pracy opiekuna (jak decyzję
  kroku); po jego wyniku decyzja wraca z `caretaker{summary, recommendation, tried[]}` (`annotate(st, plan, target)`),
- druga nieudana poprawa formatu planu (`plan-fix`, runda ≥ 2, `plan.checked` z `valid: false`, brak propozycji):
  problem bez decyzji; `retry` opiekuna na kroku `plan-fix` (`problem_steps` dopuszcza krok zakończony `done`, raz)
  daje kolejny krok poprawy z instrukcją — `_planning_result` wznawia go przez `_caretaker_resume`. Gdy opiekun
  odda problem (`escalate`, nieudany wynik albo limit działań), `build_decisions` pokazuje decyzję `plan-fix-failed`
  (`decisions._plan_fix_decisions`) z `caretaker{summary, recommendation, tried[]}`, błędami formatu w `technical`
  i możliwością „Ponów poprawę z instrukcją”: `ResolveCardStep retry` na kroku `plan-fix` zapisuje `step.retry`
  z `user: true` i instrukcją użytkownika, a `_caretaker_resume` wznawia krok jak po `retry` opiekuna. Decyzja nie
  pojawia się, gdy opiekun pracuje albo jeszcze nie oddał problemu (chyba że karta jest lokalna i opiekun nie działa).

Plan czekający na zatwierdzenie po rundach review nie jest wyzwalaczem (rekomendacja po N minutach — #3). Opiekun
nie zatwierdza planu ani różnicy: w `ACTIONS` nie ma takiej
akcji (`approve-plan` to `bad-caretaker-action`), a `return-plan` nie woła `approve_plan`.

**Akcja.** `_CaretakerPort.return_plan` woła istniejące `FlowEngine.edit_plan` z `action: return` (ta sama walidacja,
idempotencja i ochrona ticketów, które ruszyły; `no-plan` przy braku propozycji). Parametry `caretaker_problem`,
`caretaker_step` i `caretaker_from` są tylko po stronie silnika (nie ma ich w D-Bus): `plan.returned` dostaje
`caretaker`, `caretakerStep` i `caretakerFrom`, a krok `plan-amend` rusza w następnym przebiegu pętli
(`_advance_user_revision`), z uwagą opiekuna dosłownie w kartce („Uwagi: …”). Numer w `findings` spoza listy uwag
review (`caretaker.review_items`, numeracja od 1 — ta sama w kartce i w `plan-fix/review-findings.md`) to nieudane
działanie `bad-findings` z powodem; tak samo traktowane są znaki, których nie można odczytać jako indeks
(np. `²`), bez pozostawienia otwartego zamiaru akcji. Wynik w `caretaker.action` (`ok: false`) liczy się do limitu.
Powtórka po restarcie rozpoznaje własny zapis po `caretakerStep` (drugie `plan.returned` nie powstaje).

**Limit.** Każde działanie (udane i nieudane) liczy się do limitu z #8 (`ACTION_LIMIT`). Żeby kolejne odesłania nie
zaczynały limitu od zera, `plan-amend` po odesłaniu opiekuna dziedziczy łańcuch nieudanej poprawki
(`caretaker.chain_root` → `caretakerFrom`), a zakończony `done` krok poprawy planu nie zamyka problemu. Problem zamyka
dopiero decyzja użytkownika: odesłanie planu bez pola `caretaker` lub odrzucenie poprawki (`plan.revision-discarded`)
przez `FlowState.plan_user_seq`, a także jawne ponowienie kroku z tego łańcucha (`step.retry` z `user: true`, bez
`caretaker`). Po ponowieniu użytkownika kolejne odrzucenie formatu ponownie uruchamia opiekuna z nowym limitem
działań; numery jego kroków pozostają ciągłe. Po limicie decyzja wraca do użytkownika ze streszczeniem.

**Nieaktualność.** Odesłanie albo edycja planu przez użytkownika w czasie pracy opiekuna kończy jego akcję jako
`stale` (`plan.returned` bez `caretaker`, `plan.ticket-edited`, `plan.revision-discarded`): nic nie jest wykonane,
nie powstaje drugie `plan.returned`, a działanie nie liczy się do limitu.

**Kontekst i kartka.** Sekcja `BRIEF_SECTIONS["plan-fix"]` podaje błędy sprawdzania formatu, odesłane wcześniej uwagi
i uwagi review z numerami oraz każe wskazać w `feedback` wiersz, bloker i błąd standardu. Źródło `SOURCES["plan-fix"]`
dokłada do `in/context/` pliki `plan-fix/errors.md`, `previous-proposal.md`, `failed-plan.md` (plan.md odrzuconej
próby), `returned-feedback.md` i `review-findings.md`; standard planu to `docs/agent-plan-standard.md` z migawek
dokumentów. Dostawcę opiekuna karty bez dostawcy w `card.started` („Mam plan”) wskazuje planujący z propozycji.

**Historia.** „Opiekun: odesłał plan z uwagą — <powód>” z `caretaker.action`; samo `plan.returned` nie ma wpisu
w historii, więc nic się nie dubluje.

#### Luka w planie w trakcie realizacji

Przy błędzie ticketu w zatwierdzonym planie opiekun może rozpoznać brak osobnej pracy, której ticket nie powinien
wykonywać (PT-155, przypadek K-16 z 03.11). Korzysta z dotychczasowych wyzwalaczy: `error`, `repeat` lub `waiting`
dla pytania; kroki ticketu i review zachowują swoje kartki i kontrakty `result.json`. Nie ma osobnego sygnału luki.

`return-plan` wywołuje to samo `EditCardPlan` / `edit_plan` z `action: return`. Baza `plan.returned` pochodzi
z zatwierdzonego planu i ma nadane ID ticketów, a zdarzenie zapisuje `caretaker`, `caretakerStep`, `caretakerFrom`
i dosłowną uwagę opiekuna, np. „dodaj ticket na migrację formatu, wynika z decyzji A; tickety, które ruszyły,
przepisz bez zmian”. Następnie istniejąca ścieżka `plan-amend` → niezależne review → różnica czeka na użytkownika.
Opiekun nigdy nie zapisuje `plan.revision-approved`; dodane tickety dostają numery dopiero po zatwierdzeniu różnicy.
Rozpoczętych ticketów nie można zmieniać ani usuwać: działa dotychczasowe sprawdzenie poprawki i odmowa
`ticket-started` przy zatwierdzaniu różnicy. Opiekun nie ma akcji omijającej te bramki.

Odesłanie rozwiązuje problem kroku ticketu: projekcja `plan.returned` oznacza krok jako zastąpiony poprawą planu
(`superseded`), więc jego błąd lub pytanie nie wraca do decyzji, automatycznego ponowienia ani kolejnego opiekuna.
Wynik i kartka kroku zostają bez zmian, a ticket czeka na różnicę planu. Po jej zatwierdzeniu przez użytkownika
czeka jeszcze na scalenie dodanej pracy do gałęzi karty (albo jej odrzucenie przez użytkownika). Nie czeka na nowe
tickety zależne, także pośrednio, od niego samego. Przy kolejnych poprawkach projekcja sprawdza wspólny graf
jawnych blokerów i oczekiwań wszystkich wznowień. Najpierw zachowuje wcześniejsze oczekiwania, potem dodaje
nowe w kolejności kroków z dziennika i ID ticketów; pomija oczekiwanie, które zamknęłoby cykl. Przykładowo,
gdy A czeka na X zależny od B, dodanie Y zależnego od A nie zatrzyma B przed Y: B kontynuuje, umożliwiając
kolejno scalenie X, wznowienie A i wykonanie Y. Potem silnik wznawia krok przez istniejące `_resume_step`,
w tym samym worktree, z instrukcją uwzględnienia zmian z gałęzi karty i kontynuacji pierwotnego zakresu.
Oczekiwanie odtwarza się z dziennika po restarcie, a start następcy usuwa je z projekcji `plan_resumes`;
stary błąd nie wraca do decyzji. Jawne ponowienie użytkownika nadal korzysta z dotychczasowej ścieżki.
Poprawa planu ma własny łańcuch problemu; nie dziedziczy błędu ticketu.

Jeśli poprawa planu się nie powiedzie, wybór użytkownika „Odrzuć poprawkę” (`discard-revision`) kończy oczekiwanie
ticketu na tę poprawkę. Silnik wznawia jego pierwotny zakres według zachowanego planu, bez przywracania starego
błędu lub pytania. Blokery z wcześniejszych zatwierdzonych różnic nadal muszą być scalone albo odrzucone.
Ta kontynuacja także odtwarza się po restarcie z istniejącego zdarzenia `plan.revision-discarded`.

Druga luka przy już trwającej poprawie daje nieudane działanie `revision-active`, liczone do limitu, także
podczas niezależnego review nowej propozycji (przed bramką `review-pending`). Gdy użytkownik
odeśle plan albo ponowi krok po starcie opiekuna, wynik opiekuna jest `stale`: bez kolejnego `plan.returned`
i bez zużycia limitu, także dla problemu z decyzją. Historia pokazuje jeden wpis
„Opiekun: odesłał plan z uwagą — <powód>”.

`in/context/plan/approved.md` zawiera zamaskowaną migawkę zatwierdzonego planu z dziennika (ID i treść kartek),
a `plan/spec.md` — specyfikację propozycji. Dotychczasowe źródła dołączają kartki `plan/tickets/<ID>.md`,
kartkę kroku oraz jego notki i końcówkę `step.log` w `steps/<krok>/`. Migawka zatwierdzonego planu odróżnia go
od pierwotnego `plan.md` z numerami roboczymi. Wszystkie pliki podlegają wspólnemu maskowaniu i limitowi 400 kB.

### Ponowienie, limit i nieaktualne akcje (PT-150)

**Wspólna ścieżka akcji** (`FlowEngine._caretaker_act`, także z `_reconcile`): wynik kroku opiekuna (`outcome`
w `step.finished`) → sprawdzenie nieaktualności → `escalate` jako jedno zdarzenie `caretaker.escalated` albo, dla
każdej innej akcji, para „zamiar → wynik” `caretaker-action` z kluczem kroku opiekuna (`_act`) i na końcu
`caretaker.action`. Zapisany wynik pary nie wykonuje akcji drugi raz; restart między zamiarem a wynikiem dokańcza ją
tą samą ścieżką, a `apply` jest bezpieczne do powtórzenia (`step.decision` albo `step.retry` z `caretakerStep` tego
kroku opiekuna nie powstaje drugi raz). Akcja nieudana
przy wykonaniu zwraca z `apply` `{failed, error}`: wynik pary jest zapisany (`ok: true`), a `caretaker.action` ma
`ok: false`, `code` i `error`.

**Limit** `caretakerActionLimit` (1–10, domyślnie 3 — `caretaker.ACTION_LIMIT`; wartość z ustawień przez
`caretaker.setup`, „Ustawienia” niżej, PT-151) — kroki opiekuna na problem, udane i nieudane, liczone wyłącznie
przez `owned()` (`count`).
Ponowiony krok, który znowu pada, to ten sam problem (id z korzenia łańcucha), więc kolejny krok opiekuna należy do
niego. Przy `count ≥ limit` `_caretake` nie uruchamia kolejnego kroku opiekuna, tylko zapisuje `caretaker.escalated`
z `limit: true`, `failed: true`, `code: caretaker-limit`, `count`, `step` ostatniego działania i streszczeniem prób
(`caretaker.limit_summary`: akcja, powód, skutek każdego działania). Stan trzyma ją w `caretaker_limits` (nie jest
wynikiem kroku opiekuna), a `owned()` daje wtedy `handed: true`; decyzja problemu ma `caretaker.tried[]` z pozycjami
`{step, action, ok, reason, text}` (powód z pola `reason` wyniku, skutek jak w historii). Decyzja użytkownika (bez
pola `caretaker`) zamyka problem i zeruje licznik — kolejny błąd tego kroku zaczyna od zera.

**Nieaktualność** (`caretaker.stale(st, run, events)`, jedna funkcja dla obu rodzajów problemów) — sprawdzana przed
startem kroku opiekuna i przed każdym skutkiem jego wyniku (także przed oddaniem nieudanego wyniku), ale nie po
zaczętym zamiarze `caretaker-action`:

- problem z decyzją (`decision` w `caretaker.started`; zapis sprzed PT-150 — decyzja aktualnego problemu): jest
  `step.decision` kroku problemu bez pola `caretaker` („użytkownik już zdecydował”) albo `problems()` nie zwraca
  problemu z tym samym id decyzji („decyzja problemu już nie czeka”),
- problem bez decyzji: `problems()` nie zwraca już problemu („problem już nie występuje”),
- w obu przypadkach: po `watermark` z `caretaker.started` jest zdarzenie spoza opiekuna — w łańcuchu problemu
  (problem bez kroku: w jego tickecie, bez ticketu: w całej karcie) `step.started`, `step.retry` albo
  `step.decision`, a w całej karcie `card.paused`, `card.moved`, `plan.returned` albo `plan.edited` („po starcie
  opiekuna: <zdarzenie>”). Zdarzenia samego
  opiekuna nie liczą się: `caretaker.*`, zdarzenia z `kind: caretaker` (start, czekanie na slot), zdarzenia z polem
  `caretaker` (zapisy jego akcji) oraz wyniki i aktywność kroku opiekuna (nie ma ich na liście).

Akcja nieaktualna zapisuje `caretaker.action` z `ok: false, error: "stale", stale: true` i powodem w `detail`, nic
nie wykonuje (bez pary `caretaker-action`), nie oddaje problemu i nie liczy się do limitu (`owned()` pomija takie
kroki). Gdy wynik odrzucił też strażnik (wznowiony przez użytkownika krok pisał w tym samym worktree), jego powód
jest dopisany do `detail`. Wyzwalacze #5, #6 i #13 testują regułę na swoich problemach.

**Pierwszeństwo slotu** (US 37): krok opiekuna zajmuje slot dostawcy jak każdy krok (`providerSlots`,
`_wait_reason`, `_admit`, `step.waiting` z powodem „limit kroków dostawcy …”). Gotowy krok opiekuna karty
(`caretaker.started` bez startu i bez wyniku) u tego samego dostawcy rezerwuje miejsce przed każdym innym krokiem tej
karty (`_caretaker_reserved`: rezerwacja liczy się w `_wait_reason` jak trwający krok), więc nowy ticket karty czeka,
a opiekun startuje w tym samym przebiegu. Rezerwacja nie dotyczy innych kart, nie omija limitu slotów ani okna
limitu dostawcy (D3 — czeka jak inne kroki). Krok opiekuna nie wyprzedza kroków innych kart (`_earlier_waiters`):
kroki innych kart czekające na miejsce u tego samego dostawcy (`step.waiting` z `cause: slots`) wcześniej niż on
(`since` — początek czekania, zachowany przy zmianie powodu) zajmują miejsca w jego `_wait_reason` (licznik
w powodzie najwyżej `limit/limit`). Kolejność to jeden porządek wspólny dla wszystkich kart: (`since`, identyfikator
karty, ticket, rodzaj kroku) — remis w obrębie sekundy rozstrzyga identyfikator karty, więc dwaj opiekunowie
czekający od tej samej chwili nie czekają na siebie nawzajem. Zwolnione miejsce bierze więc wcześniej czekająca karta
niezależnie od kolejności pętli (`journal.cards()`). Pomijane są karty zakończone, wstrzymane, przeniesione do
Gotowe, Archiwum albo Backlogu i odrzucone tickety. Opiekun zakończony przed startem (np. `stale` po decyzji
użytkownika, gdy czekał na slot) traci czekanie: wynik (`caretaker.action`/`caretaker.escalated`) zdejmuje jego
`step.waiting` w `fold`, więc nie blokuje kroków innych kart. Zatwierdzenie różnicy planu
(`plan.revision-approved`) usuwa czekanie usuniętych ticketów w `fold`, również po restarcie usługi.
Pomijane są również zwykłe kroki, których start
blokuje rezerwacja dla opiekuna ich własnej karty według `_wait_reason`. Dzięki temu kolejność czekania
ticket A → opiekun B → opiekun A nie tworzy cyklu: startuje opiekun B, następnie opiekun A, a potem ticket A.
Opiekun nie przejmuje wcześniejszego miejsca swojego ticketu. Gdy obok rezerwacji są jeszcze miejsca dla
zwykłych kroków, ich wcześniejsze czekanie nadal liczy się w kolejce innych kart.
Od PT-377 ta sama kolejka obejmuje wszystkie kroki, a klucz porządku dostaje priorytet karty — sekcja „Kolejność
kroków czekających na miejsce u dostawcy (PT-377)”.

**Ponowienie bez decyzji w krokach planu:** `retry` opiekuna przy problemie bez decyzji (`step.retry` z `caretaker`
i `instruction`) wznawia krok wspólną funkcją `_caretaker_resume` — w zwykłych krokach (`_advance`), w planowaniu
(`_advance_planning`) i w review/poprawkach planu (`_plan_step_decision`), z instrukcją w kartce wznowienia, także
przy próbie zerowej (zamiast automatycznego ponowienia bez instrukcji).

**Decyzja użytkownika przy pracującym opiekunie:** trwający krok opiekuna nie czyni ticketu „zajętym” w
`resolve_step`, więc decyzję przyjmuje zwykłe API (StepResolve); krok wznawia `_advance`, a wynik opiekuna jest potem
nieaktualny. Decyzja zapisana akcją opiekuna ma też `caretakerStep`: dokończenie po restarcie rozpoznaje własny
`step.decision` (jak `step.retry`) przed walidacją `focus`, więc inna oczekująca decyzja karty nie zamienia
wykonanego ponowienia w porażkę.

**Ponowienie kroku przy błędzie trackera:** `resolve_step` z jawnym `step` (także od opiekuna) zapisuje decyzję
tego kroku, nawet gdy nie ma trwających kroków i czeka trwały błąd komentarza YouTracka. Globalne `retry` bez
`step` i bez problemu opiekuna nadal resetuje błędy trackera. Port opiekuna sprawdza wynik `resolve_step`:
reset trackera albo odpowiedź bez potwierdzenia ponowienia wskazanego kroku daje nieudane działanie
`retry-not-applied`, bez wpisu historii o udanym ponowieniu.

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
  `review.md`, `handoff.md`, `result.json` oraz dodatkowe notki `*.md` wskazane w `notes`/`kept` wyniku lub zdarzenia
  zakończenia (z archiwum kroku w katalogu karty), `plan-review.json` (z `<krok>/out`) i `step-log.md` —
  ostatnie 300 zdarzeń `out/step.log` po ludzku (`faults.excerpt(…, limit=300)`, jak wycinek PT-125),
- `attempts.md` — próby kroków problemu (wykonawca, wynik, powód, czyja wina, decyzja) i poprzednie działania
  opiekuna przy tym samym id problemu (wynik, streszczenie, rekomendacja, błąd, `note.md`, `result.json` i dodatkowe
  notki z `notes`/`kept` kroku opiekuna) — bez bieżącego kroku. Dodatkowe nazwy muszą wskazywać plik Markdown
  bez składników ścieżki; odczyt nie podąża za dowiązaniami,
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
rozmiaru. Tytuły plików także przechodzą przez maskowanie. Ścieżka zawierająca sekret (sprawdzana również przed
sanitacją i skracaniem nazwy) dostaje unikalny alias `masked-N.*`, z rezerwacją pozostałych nazw. Alias jest nazwą
zapisanego pliku, więc odnośnik w kartce nadal działa. W `journal.jsonl` i plikach `*.json` każdy odkodowany klucz
i każda wartość są maskowane osobno, przed ponowną
serializacją (dotyczy też wyniku osadzonego w `attempts.md`). Kolizje kluczy po maskowaniu dostają unikalny
dopisek ` [N]`, z pominięciem nazw innych pól, żeby nie nadpisywać danych. Pole, które `faults.mask`
uznaje za sekretne na próbce `nazwa=x` (`token`, `password`, `apiKey`…), traci całą wartość — także napis ze spacjami
i liczbę w dowolnie zagnieżdżonych obiektach i listach pod tym polem; pozostałe napisy przechodzą przez
`faults.mask`, a liczba zmieniona przez `faults.mask` (np. równa wartości
sekretnej zmiennej środowiska) staje się `•••`. Niepoprawny lub niepełny plik JSON jest zastępowany dopiskiem
„(pominięto: niepoprawny lub niepełny JSON)” — maskowanie surowych sekwencji ucieczki mogłoby ujawnić część sekretu.
Tak samo uszkodzone zdarzenie w `out/step.log` (linia zaczynająca się od `{`, także przy EOF po awarii) zastępuje
dopisek bez jego treści. Poprawne zdarzenie bez końcowego znaku nowej linii nadal trafia do wycinka po odkodowaniu;
odkodowane wartości zdarzenia przechodzą przez maskowanie przed łączeniem i normalizacją białych znaków,
żeby wielowierszowy sekret środowiska nadal pasował do swojej wartości. Przed rozdzieleniem stderr i zdarzeń
`faults.mask(…, multiline_only=True)` maskuje dopasowania przekraczające granice linii: poprawny JSON wewnątrz
sekretu nie rozcina go na jawne fragmenty. Jednoliniowe serializacje pozostają nienaruszone do odkodowania.
Surowy stderr jest następnie maskowany całymi blokami przed podziałem na linie, usuwaniem białych znaków
i skracaniem; odczyt zachowuje oryginalne znaki końca linii (także CRLF). Dokument `AGENTS.md` przechodzi przez
`faults.mask` z tym samym środowiskiem przed wycięciem sekcji „Przepływ karty” i normalizacją CRLF, zarówno
z kopii dokumentów Tide, jak i z worktree. Historia i poprzednie diagnozy opiekuna dostają zamaskowane kopie
zdarzeń przed formatowaniem przez `caretaker.history_text`, bez zmiany źródłowego stanu karty.
Końcówka `out/step.log` czytana od środka pliku zaczyna się od
pierwszej pełnej linii — sekret przecięty granicą odczytu nie traci nazwy zmiennej ani prefiksu tokenu przed
maskowaniem. Tak samo odczyt kończący się przed końcem pliku (początek pliku dłuższego niż `FILE_READ_MAX` albo plik
dopisywany w trakcie odczytu) odrzuca ostatnią, niepełną linię i dopisuje „dalsza część pliku pominięta” — urwana
reszta tokenu nie trafia do `faults.mask` (review PT-153, runda 2). Po odrzuceniu urwanej linii obie granice
odrzucają też pozostałe linie przeciętej wielowierszowej wartości środowiska, której sekretność rozstrzyga
`faults.mask`. Sprawdzenie zachowuje oryginalne białe znaki i bajty (bezstratne `surrogateescape`), przed
formatowaniem; przesunięta granica jest sprawdzana ponownie na wypadek nakładających się wartości (review
PT-153, runda 10). `faults.trim_secret_boundary` korzysta z tych samych wzorców co `faults.mask`, aby odrzucać
także fragmenty cytowanych przypisań bez wartości w środowisku: na początku końcówki pierwszy cudzysłów każdego
rodzaju może zamykać utraconą wartość, a przy urwanym końcu tymczasowe domknięcie ujawnia początek przypisania.
Odrzucany fragment rozszerza się na całe linie i kompletne sekrety przecięte nową granicą; może obejmować też
jawny tekst, którego nie da się odróżnić od kontynuacji sekretu. Bez dodatkowych odczytów ponad limit. Jeśli
dopisywany dziennik ma obie granice ucięte (brakuje początku i nie osiągnięto EOF), cały odczyt zastępuje dopisek
o pominięciu — brak obu cudzysłowów nie rozstrzyga wtedy, czy fragment jest środkiem sekretu (runda 11).
Odczyt tylko z korzeni: katalog karty w tablicy (`flow/<K-…>`), worktree kroku opiekuna oraz worktree
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

### Zmiana wykonawcy

Akcja opiekuna `change-executor` (PT-145) przy błędzie, powtórzonym powodzie albo braku postępu przyjmuje
`step`, `executor` (nazwę z katalogu), `reason` oraz opcjonalną `instruction` do 4000 znaków. Używa istniejącego
`resolve_step` i jego `_resume_executor`, bez drugiej implementacji reguł przydziału. Krok lokalny ani karta
„Tylko lokalnie” nie przechodzą do chmury. Opiekun musi zachować różne modele autora i recenzenta, a w trybie
mieszanym także różnych dostawców. Istniejący wyjątek dla świadomego wyboru użytkownika pozostaje dostępny
wyłącznie użytkownikowi. Wykonawca spoza katalogu albo naruszenie reguł daje nieudane działanie z powodem,
liczone do limitu; krok nie jest zmieniany.

Wznowienie zachowuje worktree i zmiany, ale uruchamia nową sesję. Opcjonalna instrukcja trafia do kartki nowego
wykonawcy. Historia: „Opiekun: zmienił wykonawcę <krok> na <nazwa> — <powód>”. Akcja korzysta ze wspólnej pary
`intent`/`result` `caretaker-action`; własny zapis `step.decision.caretakerStep` zabezpiecza restart między
skutkiem a wynikiem pary przed drugą decyzją.

### Odpowiedź na pytanie

Akcja `answer` przy czekającym pytaniu przyjmuje `step`, `answer`, `reason` i `category`: `technical` albo
`environment`. Odpowiada na pytanie kroku ticketu, review, poprawek lub konfliktu istniejącym `resolve_step`,
a wznowiona kartka oznacza odpowiedź jako pochodzącą od opiekuna. Historia: „Opiekun: odpowiedział na pytanie
<krok> — <powód>”. Odpowiedź ma od 1 do 4000 znaków; działanie podlega wspólnej aktualności, limitowi i parze
„zamiar → wynik”.

Tide odrzuca odpowiedzi dla `planning` i wszystkich `plan-*`, niezależnie od zadeklarowanej kategorii. Pytanie
zostaje użytkownikowi z rekomendacją, a nieudane działanie liczy się do limitu. Kartka każe eskalować pytania
o zakres, wymagania, wybory biznesowe oraz pytania z opcjami wariantów produktu. Kategoria nie dowodzi, że treść
jest techniczna — rozpoznanie treści pozostaje zadaniem opiekuna; zakaz odpowiedzi przy planowaniu egzekwuje Tide.

### Decyzja czeka

Wyzwalacz `waiting` (PT-145) sprawdza zegar silnika i zapisany początek czekania decyzji. Po czasie **dłuższym**
niż `caretakerDecisionMinutes` tworzy problem `waiting:<id decyzji>`. Dokładnie N minut jeszcze nie uruchamia
opiekuna. Przy pytaniu dostępne są `answer`, `escalate` oraz `return-plan` dla luki w planie (PT-155);
przy zgodzie na polecenie, zatwierdzeniu planu (także różnicy planu) i review po ostatniej rundzie — wyłącznie
`escalate`. Lista akcji jest egzekwowana przez Tide,
także przy wykonywaniu akcji. Opiekun nie tworzy zgody, nie zatwierdza planu ani nie rozstrzyga review.

Decyzja pozostaje widoczna przez cały czas pracy opiekuna; `escalate` tylko dopisuje `caretaker.recommendation`
i streszczenie. Po eskalacji ten sam problem nie uruchamia opiekuna ponownie, również po restarcie. Tożsamość
decyzji zapobiega przenoszeniu rekomendacji na kolejną decyzję tego samego kroku. Zmiana N nie unieważnia pracy
już uruchomionego opiekuna. Jeśli użytkownik rozstrzygnie decyzję w trakcie jego pracy, wynik opiekuna jest
`stale`, niczego nie zmienia i nie liczy się do limitu.

### Ustawienia

#### Wyłącznik, wykonawca, limit działań i minuty czekania (PT-151)

Cztery klucze w `$XDG_CONFIG_HOME/tide/cardflow.json`, czytane i zapisywane istniejącymi `GetCardFlowSettings`
i `SetCardFlowSettings` (sygnatury bez zmian; zapis zmienia tylko podane klucze, reszta pliku zostaje):

| Klucz | Wartość | Domyślnie |
| --- | --- | --- |
| `caretakerEnabled` | `true`/`false` | `true` |
| `caretakerExecutor` | nazwa wykonawcy z katalogu albo `""` („domyślny”) | `""` |
| `caretakerActionLimit` | liczba całkowita 1–10 — kroki opiekuna na problem | 3 |
| `caretakerDecisionMinutes` | liczba całkowita 1–1440 — minuty czekania decyzji, zanim zajmie się nią opiekun (#3) | 10 |

`GetCardFlowSettings` zwraca zawsze wszystkie cztery (wartość złego typu albo spoza zakresu w pliku — domyślna,
`caretaker.settings_of`). Walidacja jest w usłudze (`caretaker.setting_problem`), przed zapisem całego żądania:
zły typ albo zakres (np. `caretakerActionLimit` 0, 11 albo `"3"`, `caretakerDecisionMinutes` 0 albo 1441, `true`
zamiast liczby) → `bad-request`; wykonawca spoza katalogu → `bad-executor`; nieznany klucz → `bad-settings` (jak
dotąd). `limitTransfer`, `limitThresholdPercent` i `providerSlots` działają bez zmian.

`heavyLimit` (PT-373) — liczba całkowita 1–6, ile ciężkich poleceń poza piaskownicą biegnie naraz na maszynie
(sekcja „Kolejka poleceń poza piaskownicą i kolejka ciężkich poleceń”); domyślnie 2. `GetCardFlowSettings` zwraca
je zawsze (zła wartość w pliku — 2), `SetCardFlowSettings` odrzuca wartość spoza zakresu, ułamek, napis i `true`
(`bad-settings`), a zapis zmienia tylko ten klucz i zachowuje nieznane klucze pliku. Pole w oknie: „Ciężkie polecenia
naraz” na stronie „Zarządzaj” (PT-375, sekcja „PRZEPŁYW KARTY · RÓWNOLEGŁOŚĆ”).

`ticketsPerCard` (PT-375) — liczba całkowita 1–6, ile ticketów jednej karty jest w toku naraz (sekcja „Limit
ticketów w toku na kartę”); domyślnie 2. Te same zasady co `heavyLimit`: `GetCardFlowSettings` zwraca je zawsze (zła
wartość w pliku — 2), `SetCardFlowSettings` odrzuca wartość spoza zakresu, ułamek, napis i `true` kodem
`bad-settings`, a zapis zachowuje pozostałe klucze pliku. Pole w oknie: „Tickety naraz na kartę” obok „Ciężkie
polecenia naraz” (jeden zapis `SetCardFlowSettings {heavyLimit, ticketsPerCard}`).

`heavyCommands` (PT-382) — lista napisów-wzorców ciężkich poleceń (składnia i lista domyślna: sekcja „Kolejka
poleceń poza piaskownicą i kolejka ciężkich poleceń”). `GetCardFlowSettings` zwraca ją zawsze: brak klucza (albo
niepoprawna wartość w pliku) — lista domyślna; zapisana pusta lista `[]` oznacza brak wzorców (ciężkie zostają
polecenia kontraktu i próby po scaleniu). `SetCardFlowSettings` odrzuca wartość, która nie jest listą napisów,
oraz pusty wzorzec (`""`, same spacje, `*`) kodem `bad-settings` przed zapisem; zapis zachowuje nieznane klucze.

`quietCommands` (PT-376) — lista napisów-wzorców poleceń wymagających ciszy (składnia jak `heavyCommands`; sekcja
„Polecenia wymagające ciszy”). `GetCardFlowSettings` zwraca ją zawsze: brak klucza albo niepoprawna wartość w pliku —
lista pusta. `SetCardFlowSettings` odrzuca wartość, która nie jest listą napisów, oraz pusty wzorzec kodem
`bad-settings` przed zapisem; zapis zachowuje nieznane klucze.

**Wybór wykonawcy — jedno miejsce** (`caretaker.executor_name(catalog, st, preferred)`, wołane wyłącznie przez
`caretaker.setup` i `FlowEngine._caretaker_setup`; z niego korzystają start kroku opiekuna, rezerwacja slotu,
a także rozmowa z opiekunem (#7) i zmiana wykonawcy przez opiekuna (#3)):

- karta „Tylko lokalnie” — wykonawca lokalny z katalogu: `caretakerExecutor`, gdy sam jest lokalny, inaczej
  pierwszy lokalny w kolejności katalogu; chmurowy `caretakerExecutor` jest pomijany, więc kontekst karty nie
  wychodzi z komputera. Bez wykonawcy lokalnego opiekun nie startuje, a decyzja ma dopisek „opiekun niedostępny dla
  pracy tylko lokalnej”,
- inna karta — `caretakerExecutor`, jeśli jest w katalogu (wykonawca usunięty z katalogu po zapisie — domyślny),
  inaczej najmocniejsza klasa preferowanego dostawcy karty (w trybie mieszanym — dostawcy wykonawcy karty; dla
  Anthropic Opus, dla OpenAI Codex mocny).

`caretaker.setup(st, settings, catalog)` daje `{enabled, executor, reason, note, limit, decisionMinutes}`: `reason`
— dlaczego opiekun nie ruszy (wyłączony albo brak wykonawcy lokalnego; `caretaker.unavailable` go zwraca), `note` —
dopisek do decyzji (tylko brak wykonawcy lokalnego). Silnik dokłada wynik do stanu przed widokami
(`FlowState.caretaker_setup`, nie z dziennika), więc `flow.caretaker.enabled` i dopisek decyzji liczą się z tych
samych ustawień co start. Opiekun wyłączony albo bez wykonawcy lokalnego, gdy jego krok czekał już na start (np. na
slot), nie rusza: `caretaker.action` z `stale: true` i powodem w `detail` (bez eskalacji, nie liczy się do limitu),
decyzja wraca jak dziś. Krok opiekuna, który już pracuje, kończy się normalnie.

**Okno.** Strona „Zarządzaj” (`ManagePage.qml`), sekcja „PRZEPŁYW KARTY · OPIEKUN”: przełącznik „Opiekun karty”,
wybór wykonawcy („Domyślny — najmocniejszy u dostawcy karty” i cały katalog, także lokalni;
`board.js caretakerExecutorOptions`), limit działań (1–10) i minuty czekania (1–1440); „Zapisz” wysyła jedno
`SetCardFlowSettings` z czterema kluczami, a błąd usługi pokazuje się pod sekcją.

Piaskownica — „Piaskownica i strażnik” niżej. Opiekun czekający na slot dostawcy (`step.waiting` rodzaju
`caretaker`) nie daje powiadomienia o limicie — problem nadal należy do opiekuna, powiadamia dopiero eskalacja.

### Limit ticketów w toku na kartę (PT-375)

Karta nie uruchamia więcej ticketów naraz, niż pozwala limit (`cardflow/ticketlimit.py`). Masterplan dopuszcza 2
tickety na kartę, a K-36 uruchomiła 3: dotąd jedynym limitem była liczba kroków u dostawcy.

- **Ticket w toku** to ticket zatwierdzonego planu z co najmniej jednym krokiem, niescalony (`merge` bez `ok`, poza
  `imported_done`) i nieodrzucony. Liczy się także ticket czekający na decyzję użytkownika (pytanie, błąd), na review,
  poprawki i scalenie.
- **Limit efektywny:** `max-tickets` w pliku karty (1–6), a bez niego ustawienie przepływu `ticketsPerCard` (domyślnie
  2). Zła wartość w pliku karty (`max-tickets: 9`, napis) daje limit domyślny i wpis w `errors` Tablicy
  („niepoprawny limit ticketów”), bez błędu odczytu karty. `UpdateCard {maxTickets}` przyjmuje liczbę 1–6 (także napis
  z cyframi), a wartość pustą (`""`, `null`) usuwa klucz z pliku; inna (0, 7, ułamek, `true`, napis) → `{ok: false}` bez
  zmian. `CreateCard` przyjmuje `maxTickets` tak samo. Migawka karty niesie `maxTickets` (liczba albo `null`), a
  `derived.flow.ticketLimit` — `{limit, running, override}` (limit efektywny, liczba ticketów w toku, nadpisanie karty).
- **Egzekwowanie.** `_plan_next` przed startem każdego ticketu liczy tickety w toku (po każdym starcie z odświeżonego
  stanu). Przy `w toku >= limit` ticket nie startuje (nie powstaje gałąź ani worktree), a silnik zapisuje
  `step.waiting` z `kind: ticket`, `cause: card-limit` i powodem „czeka na miejsce w karcie (<n> z <limit>)” — **raz na
  zmianę powodu** (kolejne ticki z tym samym powodem niczego nie dopisują) — i przechodzi do następnego ticketu.
  Zwolnione miejsce (scalenie albo odrzucenie innego ticketu) uruchamia czekający ticket przy najbliższym ticku;
  `step.started` zdejmuje wpis czekania.
- **Zakres.** Limit dotyczy wyłącznie startu nowego ticketu w `_plan_next`. Kolejne kroki ticketu w toku (review,
  poprawki, scalenie, konflikt, opiekun) nie przechodzą przez tę bramkę, więc ticket w toku nie blokuje własnych
  kroków. Zmniejszenie limitu niczego nie zatrzymuje: nowy ticket rusza dopiero, gdy liczba w toku spadnie poniżej
  limitu. Karta „Od razu” (jeden ticket, bez `_plan_next`) działa bez zmian także przy limicie 1. Limit nie zmienia
  kolejności startu między kartami, kolejki ciężkich poleceń ani bramki testów po scaleniu.
- **Status.** `derived.tickets[]` dla ticketu czekającego na miejsce ma `status: card-limit` i
  `statusLabel: „czeka na miejsce w karcie (<n> z <limit>)”` z bieżącą liczbą i limitem (zmiana limitu odświeża napis bez
  nowego zdarzenia; `fullcard.card_limit`). Kafelek karty bez trwających kroków pokazuje ten sam powód z `step.waiting`.
- **Okno.** Strona „Zarządzaj”: „Ciężkie polecenia naraz” i „Tickety naraz na kartę” (1–6, jeden zapis
  `SetCardFlowSettings`, błąd usługi pod sekcją). Ustawienia pełnej karty: „Tickety naraz” (puste pole — ustawienie
  domyślne; zapis `UpdateCard {maxTickets}`).

### Kolejność kroków czekających na miejsce u dostawcy (PT-377)

Zwolnione miejsce u dostawcy (`providerSlots`) bierze czekający krok karty o wyższym priorytecie, a przy równym
priorytecie krok czekający dłużej — niezależnie od tego, którą kartę tick sprawdza jako pierwszą (`journal.cards()`).
Uogólnia to kolejkę opiekuna z PT-150 („Pierwszeństwo slotu”) na wszystkie kroki. Porządek liczy wyłącznie
`cardflow/stepqueue.py`; silnik tylko go woła.

- **Priorytet karty** — klucz `priority` w pliku karty (`high`, `normal`, `low`; brak albo zła wartość — `normal`).
  Karta toru silnika dostaje pierwszeństwo, gdy właściciel nada jej priorytet wysoki.
- **Klucz porządku** (mniejszy rusza pierwszy, wspólny dla wszystkich kart i totalny):
  `(podbicie, priorytet karty, since, karta, ticket, rodzaj)`. `since` to początek czekania (`step.waiting.since`,
  zachowany przy zmianie powodu); krok jeszcze nieczekający przychodzi po wszystkich czekających o tym samym
  priorytecie. Remis w obrębie sekundy rozstrzyga stały porządek (karta, ticket, rodzaj), więc dwa kroki nigdy nie
  czekają na siebie nawzajem. Podbicie jest dziś zawsze fałszywe (ustawi je ręczne podbicie, K-39 #11).
- **Start kroku.** `_admit` → `_earlier_waiters` buduje wpis tego kroku i liczy wpisy innych kart w kolejce przed nim
  u tego samego dostawcy (`stepqueue.order` + `stepqueue.ahead`). Liczą się jak trwające kroki (w powodzie najwyżej
  `limit/limit`), więc krok startuje tylko wtedy, gdy miejsc wystarcza dla wszystkich wpisów przed nim. Kroki tej samej
  karty nie czekają na siebie w tej kolejce (ich kolejność ustala dalej przebieg karty i rezerwacja opiekuna
  `_caretaker_reserved`, bez zmian). Krok modelu lokalnego ma własną kolejkę (`model_queue`) i tej nie podlega.
- **Bieżąca przyczyna, nie zapisany `cause`.** W kolejce stoją wpisy `step.waiting` z zapisanym `cause` `slots` albo
  `quota`, ale o miejscu decyduje stan z chwili decyzji: stan kart z fazy odczytu ticku, limit tygodniowy i okna
  dostawcy (`_low_limit`), pauza, decyzja o bazie, miejsce w karcie. Wpis z `cause: slots`, którego dostawca teraz
  przekroczył limit, czeka na limit i nie blokuje innych; wpis z `cause: quota`, którego limit się odnowił, wraca do
  kolejki po miejsce. Krok, który nie może ruszyć z innego powodu, nie zajmuje miejsca: karta czeka na decyzję o bazie,
  nowy ticket (bez kroku) czeka na miejsce w karcie (`max-tickets`/`ticketsPerCard`), na testy po scaleniu albo
  trwającą operację karty, a zwykły krok jest zablokowany rezerwacją własnego opiekuna (PT-150, runda 3 — bez cyklu
  czekania). Karty zakończone, wstrzymane, przeniesione do Gotowe/Archiwum/Backlogu, odrzucone i scalone tickety oraz
  trwające kroki wypadają z kolejki.
- **Koszt.** Kolejność liczona jest ze stanów kart z jednej fazy odczytu ticku (`journal.state`, bez dodatkowych
  odczytów dzienników na krok). Priorytety i limity kart pochodzą z kontekstu odczytu Tablicy bieżącego ticku
  (`BoardRead`, K-98 #3); poza tickiem (start z D-Bus) — z jednego świeżego odczytu, i to tylko wtedy, gdy u dostawcy
  czeka krok innej karty. Limit tygodniowy i rezerwacja opiekuna są liczone raz na dostawcę/kartę w jednej decyzji.
- **Jedna funkcja porządku.** `stepqueue.order` zwraca czekających w kolejności startu z bieżącą przyczyną i powodem;
  użyje jej także część `queue.steps` migawki (K-39 #10). Kolejka ciężkich poleceń (`heavyqueue`) i limit
  `providerSlots` się nie zmieniają.
- **Ograniczenie.** Wpis należy do kolejki dostawcy zapisanego w `step.waiting`. Przekazanie pracy innemu dostawcy po
  limicie (`limitTransfer`) zmienia dostawcę dopiero przy następnej próbie startu tego kroku.

### Piaskownica i strażnik (PT-158)

Granica bezpieczeństwa kroku opiekuna, wymagana przed akcjami zmieniającymi stan karty (#8): piaskownica pozwala
pisać tylko do własnego `<krok>/out`, a strażnik niezależnie od piaskownicy sprawdza, czy krok nic poza nim nie
zmienił. Piaskownice i listy poleceń zwykłych kroków są bez zmian.

**Piaskownica** (`agentcmd.Step.write_out_only`, ustawia ją silnik dla rodzaju `caretaker`):

- Katalogiem roboczym programu jest `<krok>/out` (`Background.workdir`; `Background.cwd` — worktree — zostaje do
  zatrzymania i dziennika sesji). Piaskownica Claude CLI daje zapis katalogowi roboczemu, więc tylko `out` jest
  zapisywalny (`allowWrite: [out]`), a jej puste pliki-atrapy (`.bashrc`…) lądują w `out`, nie w worktree.
- Claude: stała lista tylko do odczytu `agentcmd.read_only_tools` zamiast listy kroku — `rg`, `grep`, `cat`, `ls`,
  `find` (`READ_ONLY_COMMANDS`, także ze wzorcami nazwanymi worktree), `git -C <worktree> log|show|diff
  --no-ext-diff --no-textconv --ignore-submodules=all …` (opcje obowiązkowe: wyłączają `diff.external`,
  `diff.<d>.command`, `textconv` i inspekcję submodułów) i `git -C <worktree> status --ignore-submodules=all`
  (`READ_ONLY_GIT`),
  `Read(//<worktree>/**)` dla Read/Grep/Glob i odczyt własnych `in`/`out`
  (`board.commands.step_reads`). Lista jest w Tide, niezależna od ustawień projektu: silnik nie podaje kroku
  opiekuna listy projektu (`claude_tools`), a `agentcmd` i tak ją pomija. `--disallowedTools` (zakaz wygrywa ze
  zgodą): `READ_ONLY_BANS` — `find` z `-delete`, `-exec`/`-execdir`, `-ok`/`-okdir`, `-fprint*`, `-fls`; `rg`
  z `--pre`, `--hostname-bin`, `--search-zip`/`-z` (także połączonym, np. `-az`, `-iz`); `git` z `--output`,
  `--ext-diff`, `--textconv`, `--show-signature`; każde przekierowanie `>` — oraz zakaz edycji worktree i repozytorium.
  `autoAllowBashIfSandboxed: false`: polecenie spoza listy nie ruszy.
  Zakaz `rg *-*z*` jest zachowawczy: wildcard obejmuje spacje, więc odrzuca też bezpieczne wywołania z `z`
  w dalszym argumencie po `-` (np. `rg -n wzorzec plik.txt`); można wtedy użyć `rg` bez opcji albo `grep`.
- Programy pomocnicze gita (review M3): środowisko kroku opiekuna (`agentcmd.read_only_git_config`, dopisane do
  `GIT_CONFIG_COUNT`, wygrywa z konfiguracją repozytorium) ma `core.fsmonitor=false`, `core.pager=cat`,
  `log.showSignature=false`, `gpg.program`/`gpg.ssh.program`/`gpg.x509.program` = `/bin/false` i puste programy
  wszystkich filtrów z konfiguracji worktree (`filter.<f>.clean|smudge|process`, `required=false`; listę czyta
  usługa `git --git-dir <gitDir> config` przed startem, błąd odczytu — krok nie startuje), do tego
  `GIT_CONFIG_NOSYSTEM=1`, `GIT_PAGER=cat`. Test na prawdziwym repozytorium z `diff.external`, `textconv`,
  `diff.<d>.command`, filtrem, `core.fsmonitor` i `gpg.program` sprawdza, że żadne dozwolone polecenie gita ich
  nie uruchamia (a zwykłe `git diff` — tak).
- Submoduły (review runda 4/M1): ich konfiguracje mogą nazywać inne filtry niż konfiguracja worktree.
  Każde dozwolone podpolecenie gita ma obowiązkowe `--ignore-submodules=all`, które wygrywa także z ustawieniem
  `submodule.<nazwa>.ignore=none`; środowisko dodatkowo wymusza `status.submoduleSummary=false`.
  Zakazy `git *--ignore-submodules=all *--i*`, `git *--no-i*`, `git *--sub*`, `git *--no-sub*` wykluczają późniejsze
  nadpisanie opcji, jej skróty (`--ign`, `--i`), negację i żądania widoku submodułów. Reguły są zachowawcze:
  odrzucają również np. `--ignore-space-change`. Test z prawdziwym submodułem i filtrem skonfigurowanym wyłącznie
  w nim potwierdza uruchomienie helpera przez reprodukcję M1, odrzucenie jej przez reguły i brak helpera przy
  dozwolonych wywołaniach `status`, `diff`, `log`, `show`.
- Codex nie ma listy dozwolonych poleceń, więc opiekun u Codexa dostaje jej pusty podzbiór: narzędzia poleceń
  i kodu są wyłączone (`-c features.shell_tool=false`, `unified_exec`, `code_mode`, `js_repl` —
  `agentcmd.CODEX_NO_COMMANDS`), katalog roboczy i jedyny zapisywalny katalog to `out` (plik wyniku przez
  `apply_patch`), `/tmp` i `$TMPDIR` są jawnie wykluczone (`sandbox_workspace_write.exclude_slash_tmp=true`,
  `exclude_tmpdir_env_var=true`, bo `writable_roots` tylko dopisuje katalogi), a `--skip-git-repo-check` (także
  przy wznowieniu) — bo `out` leży poza repozytorium. Kartka mówi wtedy, że krok nie ma poleceń.
- Kartka (`caretaker.brief(…, commands=)`) wymienia listę, zakazy i strażnika.
- Zatrzymanie kroku opiekuna (`_stop`) nie robi commita WIP (`stepstop.stop_step(commit_wip=False)`): worktree
  i gałąź kroku zostają, jakie są, a ewentualny zapis kroku wykrywa strażnik.

**Strażnik** (`cardflow/guard.py`, w silniku `_guard_baseline`, `_guard_check`, `_guard_freeze`, `_guard_rebase`,
`_guard_thaw`, `_guard_verdict`):

- Stan pod strażą: drzewo worktree kroku i — przy problemie ticketu, gdy to inny katalog — drzewo worktree karty
  (wszystkie pliki, katalogi i dowiązania — śledzone, nieśledzone i ignorowane — bez katalogu `.git`; rekord:
  ścieżka, typ i prawa, rozmiar, `mtime`, `ctime`, i-węzeł, cel dowiązania; skrót sha256 w podziale na wpisy
  najwyższego poziomu), treść `<gitDir>/HEAD` każdego z tych worktree i sha gałęzi worktree kroku oraz gałęzi
  karty. `ctime` zmienia każdy zapis i nie da się go cofnąć bez roota, więc wykrywa
  też zapis tej samej treści i podmianę z przywróconym `mtime` — mocniej niż `git status` czy suma treści, bez
  czytania plików. Git nie jest uruchamiany w worktree (haki, `core.fsmonitor`): drzewo czyta Python bez
  podążania za dowiązaniami, `HEAD` — plik w katalogu gita ustalonym przez usługę, gałęzie — `FlowGit.sha`
  w repozytorium.
- Jedyny wyjątek (PT-317): `.claude/.cc-writes` w katalogu głównym worktree — plik albo katalog z zawartością,
  który zapisuje sam Claude Code, a nie model. Strażnik liczy drzewo tak, jakby tej ścieżki nie było: pomija ją,
  a sam katalog `.claude` nie ma rekordu (jego rozmiar, `mtime` i `ctime` zmienia powstanie `.cc-writes`) — wpis
  `.claude` w drzewie tworzą wyłącznie pozostałe wpisy w nim. Dzięki temu powstanie `.claude` razem z pierwszym
  `.cc-writes` (świeży worktree, np. ticket autorstwa Codex z opiekunem Claude) nie jest zmianą; niewidoczne są
  tylko pusty katalog `.claude` i zmiana jego praw. Każdy inny wpis w `.claude` ma pełny rekord, więc zmiana
  `settings.local.json`, nowy,
  usunięty albo przeniesiony plik obok `.cc-writes` nadal dają `caretaker-wrote`. Dowiązanie o nazwie
  `.cc-writes` nie jest wyjątkiem (rekord z celem), a zapis przez nie do pliku worktree widać w rekordzie celu;
  `.cc-writes` w podkatalogu też nie jest wyjątkiem. Nowy `.claude` z czymkolwiek poza `.cc-writes` jest zmianą.
- Stan bazowy: `caretaker.guard` z `cause: start` tuż przed startem programu (po przygotowaniu katalogów kroku,
  przed `step.started`) — `{step, repo, worktrees: [{worktree, gitDir, tree, head}], branches, refs, cause,
  wrote}`.
- Działania Tide w trakcie kroku opiekuna (każde `_act` tej karty z wykonaną pracą: scalenie, commit planu,
  gałęzie, start innego kroku z przywracaniem zależności, sprzątanie…) nie są zapisem kroku: przed działaniem
  strażnik najpierw zamraża jednostkę kroku (`stepstop.freeze_unit`, `systemctl --user freeze
  <jednostka>.scope` — cgroup freezer zatrzymuje proces z potomkami), dopiero potem porównuje stan i zmiany
  zapisuje jako `caretaker.guard` z `cause: check` i `wrote: [powód]` (zapis Tide ich nie przykryje; zapis kroku
  tuż przed skutecznym zamrożeniem też tu trafia — review M1 rundy 2), wykonuje działanie, bierze stan bazowy na
  nowo (`cause: <działanie>`) i dopiero wtedy odmraża krok (także po błędzie kontroli albo działania). Zmiana
  w oknie działania jest więc zmianą Tide (review M1). Gdy zamrożenie się nie uda, a proces kroku żyje albo jego stanu nie da się
  ustalić, zmiany z okna trafiają do `wrote` („zmiana w czasie działania Tide `<działanie>`, gdy kroku opiekuna
  nie dało się zamrozić — …”) — strażnik odrzuca zamiast przepuszczać; proces już zakończony nic nie pisze.
  `FlowState.caretaker_guards` — ostatni stan bazowy, `caretaker_wrote` — zebrane powody.
- Koniec kroku (`_finish_caretaker`): strażnik działa pierwszy, przed `caretaker.parse_result` i niezależnie od
  zatrzymania czy kodu wyjścia. Zmiana od stanu bazowego, wcześniej zapisane `wrote` albo brak stanu bazowego →
  wynik odrzucony z kodem `caretaker-wrote` (`step.finished.outcome.code`, bez czytania pliku wyniku), powód np.
  „krok opiekuna pisał poza swoim katalogiem wyjściowym: zmienione pliki w worktree <ścieżka>: opiekun-byl-tu.txt;
  przesunięta gałąź ticket/pt-1”. Dalej jak każde nieudane działanie: `caretaker.escalated` z `failed: true`,
  akcja nie jest wykonana, działanie liczy się w `owned()`, a historia karty ma wpis „Opiekun: wynik odrzucony,
  akcja nie wykonana — <powód>”.
- Wyjątek: polecenie Tide w worktree pod strażą (K-39, PT-372). W trakcie kroku opiekuna w worktree może biec
  uruchomienie poza piaskownicą na zgodę kroku ticketu (`make test-changed`, skrypt pomiaru) albo próba testów po
  scaleniu (`merge-check-*` w worktree gałęzi karty, K-88). Jego `build/`, `__pycache__` i wyniki testów nie są
  zapisem opiekuna (wcześniej fałszywe `caretaker-wrote`, np. K-98-care-10 i K-98-care-13 z 11.11). Logika jest
  czysta w `cardflow/guard` (`runs`, `active`, `skipped`, `excuse`); silnik podaje tylko zdarzenia dziennika
  (`_guard_outside`):
  - uruchomienie to `outside.started` → `outside.finished` tego samego kroku; worktree z pola `worktree` startu,
    dla próby testów po scaleniu z jej pseudokroku `merge.check-step`. Start bez `worktree` niczego nie wyłącza;
  - liczy się, gdy start jest w dzienniku przed sprawdzeniem (dziennik czytany przed migawką), a koniec jest po
    stanie bazowym (`seq` ostatniego `caretaker.guard`) albo go nie ma — także polecenie, które ruszyło i skończyło
    się w trakcie kroku opiekuna;
  - worktree porównywane po `os.path.normpath(os.path.abspath(…))`, bez podążania za dowiązaniami (końcowy `/`
    i `./` pasują, dowiązanie do worktree — nie);
  - wyłączone są tylko zmiany drzewa (`tree`) tego worktree: zamiast `wrote` Tide zapisuje `caretaker.guard`
    z `cause: outside`, `wrote: []` i `skipped: [{run, worktree, entries}]` (nazwa uruchomienia, worktree,
    zmienione wpisy najwyższego poziomu) i bierze drzewo tego worktree na nowo. `HEAD` worktree, refy gałęzi,
    zniknięcie worktree oraz zapis w innym worktree pod strażą albo w worktree bez polecenia są wykrywane jak
    dotąd;
  - ta sama reguła działa w `_guard_check`, w `_guard_rebase` (krok niezamrożony; przy zamrożonym całe okno
    działania i tak należy do Tide, `_guard_freeze` bez zmian) i w `_guard_verdict`, więc zmiana pominięta
    w jednym miejscu nie wraca jako `wrote` w drugim. Stare dzienniki bez `skipped` czytają się bez zmian;
  - granice: opiekun nie ma kontraktu `outside`, więc sam nie otworzy wyjątku — polecenia w worktree uruchamia
    wyłącznie Tide (zgoda kroku ticketu, bramka testów po scaleniu). Zapis opiekuna w drzewie worktree
    z trwającym poleceniem jest w tym oknie nieodróżnialny od zapisu polecenia i przechodzi. Uruchomienie bez
    `outside.finished` (np. utracone po awarii usługi) wyłącza drzewo tego worktree do końca kroku opiekuna.
    Polecenia uruchomione ręcznie przez koordynatora (Tide ich nie zna) nadal dają `caretaker-wrote`.
- Ograniczenie: gałąź karty przesunięta w trakcie kroku opiekuna przez inny krok (np. rozwiązanie konfliktu
  innego ticketu w worktree karty) nie jest działaniem Tide, więc odrzuca wynik opiekuna (bezpieczna pomyłka —
  problem wraca do użytkownika z powodem). Tak samo działanie Tide przy kroku, którego nie dało się zamrozić
  (np. systemd bez `freeze`).

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

## Pudełko karty: profil środowiska

Moduł `tide_daemon/box/profile.py` (PT-165, karta K-38) opisuje środowisko pudełka: schemat `tide-box: 1`,
klasyfikację ryzyka, scalanie profilu projektu z profilem karty, hash przepisu oraz zapis i odczyt w Tablicy.
Bez wykrywania stosu i szablonów (#30) i bez budowy pudełka (#4). YAML czyta własny podzbiór
(`box/yamlmini.py`: mapy i listy blokowe oraz w nawiasach, cudzysłowy, komentarze), bez biblioteki.

### Schemat `tide-box: 1`

| Klucz | Typ | Uwagi |
| --- | --- | --- |
| `tide-box` | `1` | wymagany |
| `project` | napis | nazwa projektu (`[a-z0-9._-]`) |
| `type` | `container` \| `vm` | `vm` tylko przechodzi walidację, budowa odmawia (#4) |
| `base` | napis | obraz bazowy, np. `images:ubuntu/24.04` |
| `stacks` | lista | nazwy szablonów (lista szablonów należy do #30) |
| `node.version`, `dotnet.sdk` | napis/liczba, lista dla `dotnet.sdk` | |
| `dotnet.restore-projects` | opcjonalna niepusta lista względnych ścieżek `.csproj` | publiczny restore tylko wskazanych projektów na kopii HEAD; bez listy restore całego rozwiązania; lista wchodzi do hasha przepisu |
| `apt`, `setup`, `verify` | listy napisów | |
| `network.domains` | lista hostów | bez URL-i; adresy wewnętrzne (w każdym zapisie: IPv4 zmapowany w IPv6, `10.1`, liczbowe/szesnastkowe, loopback, link-local, `100.64/10`, prywatne IPv6 także z osadzonym IPv4 — mapowanym, zgodnym wstecznie i NAT64, `localhost`, nazwy jednoetykietowe oraz sufiksy `.local`, `.lan`, `.internal`, `.home.arpa`, `.localdomain`, `.intranet`, `.corp`) tylko w profilu karty; symbol `*.` wymaga co najmniej dwóch etykiet po nim (`*.com` odrzucone w profilu projektu) |
| `network.host-ollama` | bool | |
| `network.full`, `network.full-consent` | bool | tylko profil karty; `full: true` wymaga `full-consent: true` |
| `ports.pool` / `ports.env` | `[od, do]` (1024–65535) / lista nazw zmiennych | |
| `docker.enabled` / `docker.images` | bool / lista | |
| `resources.cpu/memory/disk` | liczba / `24GiB` / `60GB` | domyślnie 8 / 24GiB / 60GB |
| `resources.swap` | bool | domyślnie false; true odmawia startu w projekcie ograniczonym, poza hashem przepisu |
| `packs`, `accepted_red` | listy | tylko profil karty |
| `box` | `per-project` \| `per-card` | domyślnie `per-project` |
| `notes` | napis | |

Nieznany klucz na dowolnym poziomie, zły typ, `packs`/`accepted_red`/`network.full`/adres wewnętrzny w profilu
projektu oraz `type` spoza `container|vm` to `ProfileError` (lista wszystkich problemów).

### Klasy ryzyka

`classify(item, known)`; `known` to słownik domena → klasa z szablonów stosów (#30).

- **czerwone**: `network.full`, `packs` (`pack:<nazwa>`), adresy `192.168.*`, `10.*`, `172.16–31.*`, `*.local`, `*.lan`, `*.internal`, `*.home.arpa`,
  `*.localdomain`, `*.intranet`, `*.corp`, nazwy jednoetykietowe, `*.firma.example`, `localhost`, loopback, link-local (`169.254.*`), `100.64/10`, prywatne IPv6 i inne zapisy
  tych adresów, symbole `*.<jedna etykieta>` (np. `*.com`), zdalne repozytoria (`github.com`, `*.github.com`, host z etykietą `gitlab`),
- **żółte**: `host-ollama`, domeny oznaczone jako żółte w szablonach, domeny spoza szablonów bez cech czerwonych,
- **zielone**: domeny oznaczone jako zielone w szablonach, `box: per-card`.

Czerwone reguły są sprawdzane przed `known`, więc szablon nie może „odczerwienić” pozycji (może ją najwyżej
zaostrzyć wpisem `red`).

### Scalanie

`merge(project, card)` → `MergeResult(profile, dropped_red, project_red)`. Listy sumowane bez duplikatów (najpierw projekt; także `ports.env`),
skalary i pola map z karty wygrywają (w tym `box`); wyjątek: `ports.pool` to zakres `[od, do]` i karta go zastępuje.
Pozycje domenowe w `accepted_red` i klucze `known` są normalizowane jak domeny (małe litery, bez końcowej kropki). Czerwone pozycje karty trafiają do wyniku tylko, gdy
`accepted_red` karty je wymienia; pozostałe lądują w `dropped_red`. `accepted_red` z pozycją, której nie ma w
profilu karty (albo nie jest czerwona), to błąd. Czerwone pozycje profilu projektu (np. `github.com`) przechodzą, ale trafiają do `project_red`, żeby pakiet zgód (#9) je pokazał. `project` w profilu musi zgadzać się ze slugiem (`save_project`/`load_project`). Profil projektu nigdy nie zawiera `network.full` ani adresów wewnętrznych.

### Hash przepisu

`recipe_hash(profile, templates_version, agents_version)` = SHA-256 znormalizowanych kluczy (po wypełnieniu
wartości domyślnych, niezależnie od kolejności kluczy i białych znaków YAML) wraz z wersją szablonów i agentów.
Wchodzą: `type`, `base`, `stacks` (kolejność), `node`, `dotnet` (wersje SDK jako zbiór), `apt` (jako zbiór), `setup` (kolejność),
`docker.enabled`, `docker.images` (jako zbiór). Nie wchodzą: `resources`, `ports`, `network.*`, `verify`, `packs`,
`box`, `notes`, `project`, `tests.changed`, `tests.full` — limity i politykę czyta się przy starcie albo po budowie,
a kontrakt testów (K-69) nie dotyczy obrazu. Lista: `HASH_KEYS` i
`NON_HASH_KEYS`, test pilnuje, że razem pokrywają cały schemat. `recipe_stamp(...)` dodatkowo zwraca skróty
kluczy; budowa zapisuje go przy migawce, a `diff(profile, stamp, templates_version, agents_version)` zwraca listę
kluczy różniących się od zapisanego odcisku (`apt`, `stacks`, `templates`, `agents`…) — źródło stanu „do przebudowy”.

### Zapis

Katalog Tablicy: `$TIDE_BOARD_DIR` (domyślnie `~/DevWorkspace/tide-board`). Profil projektu:
`projects/<slug>/box.yaml`, profil karty: `projects/<slug>/cards/<id karty>/box.yaml`. Zapis jest atomowy (plik
tymczasowy w tym samym katalogu, `fsync`, `rename`) i poprzedzony walidacją; nazwy ścieżek są ograniczone do
`[a-z0-9._-]`. Profil projektu dzieli plik z kontraktem testów K-69 (`tests`); zasady wspólnego pliku — „Zgodność
z profilem pudełka K-38” w sekcji kontraktu testów. `repo_proposal(worktree)` czyta `.tide/box.yaml` z repozytorium wyłącznie jako propozycję
(walidacja jak profilu projektu, dowiązanie poza repozytorium odrzucane) i niczego tam nie zapisuje — profil
nie trafia do repozytoriów projektów (CLAUDE.md firma).

### Sekcja środowiska w prompcie planowania (PT-190)

`cardflow/environment.py` składa sekcję, którą `planning.prompt` (Lekkie) i `planning.full_prompt` (Pełne) wstawiają
przed krokiem „Poznaj repozytorium” / „Pracuj w tej kolejności” (parametr `environment`; pusty = prompt bez zmian).
Sekcję liczy `engine` przy budowie promptu planowania (także poprawek planu); nie zmienia `_launch`, `agentcmd` ani
uruchamiania kroków. Błąd odczytu plików nie zatrzymuje planowania — sekcja wtedy znika.

- **Środowisko projektu (wykryte)**: fakty `StackReport` (`tasks/stack.detect`, YAML bez pustych pól) i propozycja profilu
  w `tide-box: 1`. Źródło propozycji: `.tide/box.yaml` repozytorium (`repo_proposal`, oznaczone „z repozytorium
  (.tide/box.yaml)”; niepoprawny plik → szablony z uwagą) albo szablony stosów (`propose_profile`, „z szablonów Tide”,
  z listą pozycji sieci i obrazów; pozycje czerwone są wyłączone). Dane z repozytorium są w prompcie cytowanymi
  danymi, nie poleceniami. Każdy blok jest ucinany do 8 KB, instrukcje na końcu nigdy.
- **Środowisko projektu (zatwierdzone)**: gdy Tablica ma `projects/<slug>/box.yaml` — profil i hash przepisu
  (`recipe_hash` bez wersji szablonów i agentów; te dokłada budowa pudełka). Sekcja „wykryte” dochodzi tylko wtedy,
  gdy wykryte stosy różnią się od zatwierdzonych.
- **Instrukcja dla planującego**: może poprawić propozycję w `out/box.yaml`; nie włącza pozycji czerwonych i nie
  dopisuje ich do `accepted_red`; środowisko ustala w planie, nie pyta o nie w trakcie kroków. Dla projektu bez wzorca
  (`stacks: []`, propozycja pusta) dodaje do planu (spec.md, Further Notes) dokładnie jedno pytanie „Czego potrzebuje
  środowisko?”.

### `out/box.yaml` w propozycji planu

`plans.read` czyta `box.yaml` obok `plan.md` (tylko dla pliku o nazwie `plan.md`; plany w repozytorium dzielą katalog)
i waliduje go schematem profilu karty z `box/profile.py` (`parse(..., "card")` + `merge`), jak tabelę ticketów.
Błędy formatu trafiają do `Proposal.errors` z prefiksem `box.yaml:`: nieznany klucz, zły typ, `network.full` bez
`full-consent`, pozycja czerwona (adres wewnętrzny, `github.com`, `packs`) bez jawnego `accepted_red`, dowiązanie
poza katalog planu, plik większy niż 64 KB. Brak pliku nie jest błędem (`Proposal.box` to `None`); poprawny plik
wraca w `Proposal.box`. Plik nie jest zapisywany ani kopiowany do Tablicy — zatwierdza go użytkownik.

### Pakiet zgód: środowisko karty (PT-172)

Przy `box.enabled: false` sekcji nie ma (zgoda środowiska nie obowiązuje, PT-290; 09.11: jej liczenie przy każdym
odświeżeniu wczytywało Tablicę ponad sto razy) — wyjątkiem jest karta, która już czeka na środowisko.
Plan do zatwierdzenia (i plan zatwierdzony z „Tylko plan”) ma w `derived.flow.plan.environment` sekcję „Środowisko
karty”, liczoną przez usługę (`cardflow/envconsent.py`) z profilu projektu (zatwierdzonego albo propozycji z PT-190)
i propozycji `out/box.yaml` (zapisywanej w `plan.proposed.box`). Panel tylko ją pokazuje — nie zna listy kluczy ani
reguł ryzyka. Pola sekcji:

- `state` / `stateLabel`: `clean` (z datą migawki), `build` („do zbudowania”), `rebuild` („do przebudowy”) i `diff`
  `{added, removed}` — różnica pozycji względem profilu zatwierdzonego przy hashu migawki `clean` (dziennik pudełka),
- `items[]`: `id`, `label`, `group`, `risk` (`green|yellow|red` z `box/profile.py`, `display_items`), `riskLabel`, `token`
  (nazwa koloru motywu), `scope` (`projekt|karta`), `enabled`, `needsConsent` (czerwone: domyślnie wyłączone, włącza je
  tylko jawne zaznaczenie), `locked`, `note`; pozycja `docker` niesie opis ryzyka K1 z propozycji (szablon docker)
  i dopisek „K3 aktywne/nieaktywne” według `box/k3.py`; pozycja `box:per-card` ma `toggle {on, off}` (domyślnie jak
  w profilu projektu),
- `yaml` (profil karty bez `box` i zgód, do pola „Zmień”), `acceptKey` (`accepted_red` — klucz zgody, który panel dopisuje
  do YAML-a z zaznaczonych pozycji), `notes[]` (np. odrzucony adres wewnętrzny w `.tide/box.yaml`, D13),
- `question`: „Czego potrzebuje środowisko?” dla projektu bez profilu i bez wzorca — pozycja planu, nie pytanie w kroku.

**Koszt odświeżenia (K-98 #1, PT-342).** Jedno odświeżenie BoardMonitor wczytuje Tablicę najwyżej raz: `publish`
tworzy krótkożyjący kontekst `board/readcontext.BoardRead` i przekazuje go jawnie do migawki plików
(`BoardStore.snapshot(board=…)`) i projekcji kart (`flow.derived(card, read)` → `engine._environment_view` →
`envconsent`/`projectconsent`; także `_project`, `_review_policy`, `_plan_review_policy`). Kontekst nie jest
globalny, nie trafia do workera mutacji i ginie z końcem odświeżenia; funkcje bez kontekstu czytają Tablicę świeżo
jak dotąd (zatwierdzenie zgody, start kroku; tick ma własny kontekst — K-98 #3 niżej). Sekcja ma pamięć `engine._env_cache` wspólną dla wszystkich
projektów, także plasma-tide, z kluczem z odcisku wejść (`envconsent.view_key`: projekt i repozytorium karty, rejestr
repozytoriów, dziennik karty i wersja planu, `boxWait`, wyłącznik i plik ustawień, profile projektu i karty, dziennik
pudełka, `.tide/box.yaml`, magazyn zgody projektu i kopii paczek, tryb K3/K1) — każda zmiana wejścia z tej listy
unieważnia wpis przy następnym odświeżeniu. **Ograniczenie:** klucz składa się z metadanych plików i katalogów
(inode, rozmiar, mtime, ctime, tryb), bez czytania treści. Nie obejmuje treści źródeł paczek (`packs.inspect`: glob,
liczba plików, rozmiar, trafienia skanera) ani treści plików repozytorium czytanych przez `stack.detect`
(np. `package.json`), poza katalogiem głównym repozytorium i `.tide/box.yaml`. Zmiana takiego pliku bez zmiany
metadanych katalogu głównego repozytorium nie odświeża podglądu do najbliższej zmiany innego wejścia. Magazyn kopii
paczek jest przechodzony raz na projekt w jednym odświeżeniu (`BoardRead.once`), najwyżej 4096 wpisów; powyżej
klucz nigdy nie trafia i sekcja jest liczona w każdym odświeżeniu. Pamięć służy wyłącznie prezentacji:
`ApproveCardEnvironment` i bariery pudełka liczą odcisk, zawartość paczek (`blocked`) i prawa świeżo, więc stary
podgląd (`exposure-expected`) po zmianie wejścia jest odrzucany. Pomiar: `daemon/tests/perf_board_refresh.py`
(metody Snapshot, Work, Badge, GetCardFlowSettings, RenderFlow i BoxStatus z atrapą `incus`;
porównanie bazy z HEAD tym samym reproduktorem: `daemon/tests/perf_board_refresh_compare.sh`).

**Bramka czasów odpowiedzi K-98 (PT-345).** `bash daemon/tests/perf_gate_k98.sh <katalog>` mierzy jeden scalony
kandydat (HEAD): reproduktor PT-342 bez zmian (baza karty `a95d7a31` vs HEAD, bez obciążenia i przy zajętym workerze
`--busy-ms 2000`), 5 osobnych zimnych startów i p95 pierwszego wywołania serii D-Bus na prawdziwej usłudze z sondą
procesów (`daemon/tests/perf_dbus_first_call.py`, 20 serii, początek ticketu vs HEAD). Próba jest ważna tylko przy
1-min `/proc/loadavg` < liczba rdzeni / 2 przed i po; najwyżej 3 próby, rozstrzyga pierwsza ważna. Potem regresje
PT-342…PT-345 i `box.enabled=false` na tym samym HEAD, a `perf_gate_k98_eval.py` ocenia progi (każde odświeżenie
≤ 500 ms i ≤ 1 wczytanie Tablicy, p95 < 1 s dla Snapshot, GetCardFlowSettings, BoxStatus i pierwszego wywołania serii).

**Tick i odzyskiwanie bez powielania odczytów (K-98 #3, PT-344).** `engine.tick` ma jedną niezmienną fazę odczytu
historii (`trialhold.HistoryPhase`): rozpoznanie niepełnej historii po restarcie, odtworzenie `step.started`
(`_recover_started_steps`) i uzgodnienie sieci pudełek (`boxstep.recover_network`) dzielą jeden pełny ścisły odczyt
karty z kontrolą tożsamości (`read_process_history(journal, card, phase)`; błąd i stan unknown są zapamiętane w fazie
i nie znikają). Zapis tego procesu do historii karty (`Journal.generation`) unieważnia jej odczyt — następne użycie
czyta ponownie. Faza nie zastępuje odczytu przed skutkiem: zapis odtworzonego startu idzie w `Journal.unchanged`
(pod blokadą świeży pełny odczyt musi być równy odczytowi decyzji, inaczej `HistoryChanged` bez zapisu), a przed
pierwszą mutacją karty w `recover_network` (zwolnienie portów, refguard i zasoby kroku, polityka sieci) świeży
odczyt porównuje `HistoryPhase.unchanged`; przy zmianie mutacje tej karty są odmówione, stan sieci nie jest
zapamiętany i kolejny tick uzgadnia go od nowa. Faza trwa cały tick: `_recover_started_steps` wołane z `_tick_card`
też z niej korzysta. Pozostałe bariery karty (inwentaryzacja, kontrola procesu, outside) czytają historię poza fazą;
od K-105 PT-415 odczyty bez skutku korzystają z pamięci historii procesów i stanu procesu karty, a bramki przed
skutkiem czytają świeżo (sekcja dziennika karty, „Pamięć historii procesów i stanu procesu karty”). Faza jest
widokiem jednego ticku nad tą pamięcią. Porównania przed zapisem (`Journal.unchanged`, `HistoryPhase.unchanged`)
i zapisy bramek (`append`, `append_complete`) czytają świeżo (`Journal.read_fresh`). Tablica:
tick tworzy jeden `BoardRead` (kontekst z #1) i przekazuje go jawnie do `_project_base` (bez zmiany pól bazy — bez
transakcji i wczytania), `_tick_card` (wykrycie ruchu karty, `_project`, `_tracker_notes`), `_queue_notices`,
`_caretake` i `_guard_card` (kolumna karty). Zapis pliku Tablicy w tym procesie (`BoardStore.generation`) unieważnia
kontekst ticku (`BoardRead(store, invalidate_on_write=True)`); kontekst odświeżenia BoardMonitor (domyślny) tego nie
robi — trzyma jedną wersję Tablicy przez całe odświeżenie z jednym wczytaniem, także gdy tick równolegle zapisuje
Tablicę (kontrakt #1). Ścieżki ze skutkiem ubocznym (`moved`, start i wznowienie kroku, scalenie, start opiekuna) czytają Tablicę
świeżo jak dotąd. Pomiar: `daemon/tests/perf_tick_recovery.py` (restart z żywym właścicielem, martwym procesem
i stanem unknown; liczniki pełnych odczytów i wczytań Tablicy; porównanie bazy z HEAD:
`daemon/tests/perf_tick_recovery_compare.sh`).

**Odczyty podczas zajętej kolejki mutacji (K-98 #2, PT-343).** Kolejka mutacji (jednowątkowy worker `tide-cardflow`)
pozostaje jedynym miejscem zapisów przepływu karty; proste odczyty `/Board` na nią nie czekają:

- `Snapshot()` (także Panel karty — `cards[].derived.flow`) oddaje ostatnią **opublikowaną** wersję. Publikacja
  (`BoardMonitor.publish`/przeliczenie) idzie pod bramką `CardFlowMonitor.read_gate()` i **nie bierze blokady
  dziennika**: dzienniki kart czyta w trybie prezentacji (`Journal.presentation()` — własna pamięć, kopia usługi jest
  tylko dopisywana, plik w tablicy podmieniany atomowo, urwany ostatni wiersz trwającego dopisania pomijany).
  Spójność względem akcji daje licznik epok (seqlock): akcja z kolejki (wszystko poza tickiem) podbija go na czas
  wykonania; publikacja w trakcie akcji albo taka, w której czasie akcja się zaczęła lub skończyła, jest odrzucana
  przed jakimkolwiek skutkiem i wraca co `PUBLISH_RETRY_MS` (50 ms) — żadna wersja nie pokazuje akcji w połowie
  (np. `card.moved` bez `card.paused`), a odczyt dostaje poprzednią wersję. Zapisy ticku i recovery widać zapis po
  zapisie od razu (każdy pełny), jak przed zmianą — tick nie wstrzymuje publikacji, więc Snapshot po zapisie ticku
  widzi go deterministycznie, nawet gdy tick dalej trzyma blokadę. Granica spójności: „cała akcja albo nic” dotyczy
  akcji z kolejki. Tick nie jest akcją, więc jedna wersja może złożyć Tablicę z chwili po jednym zapisie ticku
  (np. kolumna karty) z dziennikiem sprzed jego kolejnego zapisu (albo odwrotnie) — każdy z nich pełny, jak przed
  K-98 #2; atomowość całego ticku to zakres #3. Synchronizacja zmian z zewnątrz (`sync`) bierze
  blokadę bez czekania i odkłada commit do następnej okazji.
- Wczytanie Tablicy w publikacji (`store.snapshot` i projekcja kart: widok środowiska, polityka review) też **nie
  czeka**: idzie w `BoardStore.without_waiting()`, gdzie transakcja Tablicy trzymana przez inny wątek (worker:
  StartCard w `git worktree add`, scalenie, tick) albo flock innego procesu (zapis profilu pudełka K-38) daje od razu
  `BoardBusy`. Taka publikacja jest odrzucana w całości (także gdy wyjątek złapano niżej i podano treść zastępczą;
  pamięć widoku środowiska jej nie zatrzymuje) i wraca po `PUBLISH_RETRY_MS`; Snapshot oddaje poprzednią wersję.
  Publikacje w tle (zegar przeliczenia, doręczanie wyników, ponowienia) nie czekają wcale; jawny `Snapshot()` czeka
  na transakcję Tablicy najwyżej `SNAPSHOT_WAIT_S` (250 ms, jeden termin na wywołanie), więc krótka transakcja ticku
  (np. `project_card_base`) nie zostawia odczytowi po zapisie ticku wersji sprzed zapisu, a długa mutacja daje
  poprzednią wersję po terminie. Tak samo bez czekania idą zapisy Tablicy z zegara w wątku głównym: synchronizacja
  zmian z zewnątrz (`store.sync`, `mark_ready_specs` — przy zajętej Tablicy wracają w następnej synchronizacji)
  i utrwalanie połączeń kart z sesjami (`link_session` — wraca przy następnym przeliczeniu).
- Wynik akcji jest odsyłany po publikacji jej skutku i commicie dziennika (FIFO; commit przy zajętej blokadzie
  ponawiany co 20 ms bez blokowania wątku głównego, migawka publikowana od razu i ponownie po commicie), a worker
  bierze następne zadanie dopiero po tym doręczeniu (najwyżej `DELIVERY_WAIT_S` = 5 s czekania, bez blokady
  dziennika). `Snapshot()` najpierw doręcza zakończone, a niedoręczone wyniki (bez czekania).
- Odpowiedź ma na początku `version` (numer publikacji; rośnie przy każdej zmianie treści albo `boxWait` kart, ten sam
  stan zachowuje numer) i `readState: "ready"`. Przed pierwszą publikacją: `{"version": 0, "readState": "preparing",
  "cards": [], …}` — nie pusta, gotowa Tablica; `Work()` ma wtedy `readState: "preparing"`, a `CardDocs` zwraca
  `error: "preparing"`. Okno Tablicy (`apps/board/backend.py`) nie zastępuje taką odpowiedzią posiadanej migawki,
  a bez migawki nie zapisuje jej jako Tablicy (okno zostaje w stanie „ładowanie” do pierwszego `Changed`).
  Tak samo pozostali konsumenci `Snapshot` (PT-385): widżet Tablica (`widgets/board`, `BoardSnapshot.qml`), lista
  „Karty w przepływie” w widoku Sesje (`apps/sessions/backend.py`, `flowCardsLoading`) i projekty w oknie Sprawy
  (`apps/issues/backend.py`, `projectsLoading`) zachowują poprzednią migawkę, a bez niej pokazują „wczytywanie” do
  najbliższego `Changed` (wysyłanego przy pierwszej publikacji), po którym odczytują `Snapshot()` ponownie.
- `GetCardFlowSettings` czyta plik ustawień od razu (zapis atomowy) i dodaje `version` (skrót bajtów pliku, z których
  powstała odpowiedź; `missing` — brak pliku) oraz `readState: "ready"`.
- `BoxStatus`/`BoxCardStatus` biegną w osobnej puli odczytu `tide-board-read` (nie w kolejce mutacji) na opublikowanej
  wersji (`BoardMonitor.published()`: projekty, karty i `boxWait` kart z jednej publikacji) i niosą jej `version`.
  Stan pudełka i slotu to bieżący odczyt hosta; slot przez `admission.view` — to samo uzgodnienie z Incusem co
  `admission.snapshot`, ale **bez zapisu** `state.json` (zapisują go zlecenia pudełek i tick). Przed pierwszą
  publikacją: `{ok: false, error: "preparing", readState: "preparing", state: "unknown"}`.
- Dane odczytu służą wyłącznie prezentacji: zapisy (zgoda, start, scalenie) idą kolejką i przed skutkiem sprawdzają
  historię i prawa świeżo. Ciężkie podglądy Gita (`CardBranchDiff`, `PreviewCardMerge`, `PreviewCardBaseChange`, …)
  zachowują własny kontrakt w kolejce i nie są prostymi odczytami.
- **Ograniczenie:** zapisy Tablicy z wątku głównego (`CreateCard`, `UpdateCard`, `MoveCard`, …) nadal czekają na
  transakcję Tablicy, jeśli trzyma ją długa mutacja (np. StartCard). `Worktrees` (`busy_places`) czyta dziennik pod
  blokadą jak dotąd. Odczyt prezentacji pomija tylko blokadę; `Journal.read_complete` i jego parser są bez zmian
  (urwany wiersz nadal daje błąd ścisłego odczytu), a zapisy i bramki czytają pod blokadą jak dotąd. Ścisłe czytniki
  bramek (`read_complete`, `cards_complete`) nie mają trybu prezentacji: wywołane w nim zgłaszają
  `StrictReadInPresentation` (błąd programu, nie „unknown”) zamiast czytać bez blokady albo czekać na mutację.

`ApproveCardEnvironment(card, yaml, onlyPlan)` (JSON; błędy jak inne metody /Board):

1. `prepare`: `yaml` jest profilem karty (`profile.parse(…, "card")`). Nieznany klucz, zły typ, pozycja czerwona
   (`network.full`, adres wewnętrzny, `github.com`/`gitlab`, `packs`) bez wpisu w `accepted_red`, wpis `accepted_red`
   bez takiej pozycji → `{ok: false, error: "invalid-environment", message, errors[], items[]}` **bez żadnego zapisu,
   bez zatwierdzenia planu i bez budowy** (też gdy wołane spoza panelu). Projekt jeszcze bez profilu: usługa sama
   buduje propozycję (`.tide/box.yaml` albo szablony stosów), a jej pozycje czerwone też wymagają zgody; adres
   wewnętrzny w profilu projektu jest odrzucany (D13).
2. Plan albo poprawka planu czekająca na zatwierdzenie jest zatwierdzana (`ApproveCardPlan`; PT-290: także różnica
   zatwierdzonego planu z okna, w którym sekcja środowiska zastępuje przycisk planu); `review-pending` przerywa
   przed zapisem.
3. `onlyPlan = true`: nic nie jest zapisywane w Tablicy; przy `box.enabled: true` zdarzenie `box.wait` ustawia
   `boxWait: environment`, przy wyłączonym pudełku zgoda jest tylko odroczona (D1 niżej).
4. `onlyPlan = false`, `box.enabled: true`: zapis profilu projektu (jeśli nie był zatwierdzony) i karty do Tablicy
   (`projects/<slug>/box.yaml`, `…/cards/k-n/box.yaml`), zdarzenie `box.approved source=ui` z `accepted_red`, `items`
   i `recipe`, wyczyszczenie `boxWait`, a gdy pudełko nie ma migawki `clean` z tym hashem — budowa (`build`) albo
   przebudowa (`rebuild`) przez `Builder` z PT-167 w osobnym wątku (atrapa: `FlowEngine.box_launcher`). Odpowiedź:
   `{ok, saved[], acceptedRed[], build: "build"|"rebuild"|"", boxWait: "", boxEnabled: true, decision: "recorded"}`.
5. `onlyPlan = false`, `box.enabled: false` (PT-290): po tej samej walidacji zapis samej decyzji — profile w Tablicy
   i `box.approved` — bez kopii paczek, budowy, startu, Incusa i ekspozycji; `boxWait` bez zmian (także dawne
   `environment`), zgoda odroczona. Odpowiedź: `{ok, saved[], acceptedRed[], build: "", boxEnabled: false,
   decision: "recorded", boxWait, message}`; `message` mówi, że budowy nie zlecono i środowisko nie działa.

**D1 (PT-290, K-38 F2): zgoda środowiska tylko przy `box.enabled: true`.** Zatwierdzenie nowego planu
(`engine.approve_plan`, `ApproveCardPlan`) i poprawki planu („Tylko plan”) przy wyłączonym pudełku nie tworzy
oczekiwania `environment` i nie blokuje hostowego kroku (wyłącznik PT-289 i tak wybiera hosta). W dzienniku zostaje
`box.wait` z pustym `value` i `deferred: environment` (`FlowState.box_env_deferred`, `boxwait.require_environment`).
Po ponownym włączeniu pierwsza próba kroku wykonawczego zamienia odroczenie na `boxWait: environment`
(`source: box-enabled`) i czeka — samo zatwierdzenie planu ani decyzja zapisana przy wyłączonym pudełku nie są zgodą
na budowę i ekspozycję. Dopiero `ApproveCardEnvironment` przy włączonym pudełku czyści oczekiwanie i zleca budowę.
Zgoda projektu plasma-tide (`projectconsent`) przy wyłączonym pudełku zapisuje decyzję bez `environment`
(odpowiedź `boxEnabled: false`); przy włączonym bariera ekspozycji v1 obowiązuje jak dotąd. Restart z dawnym
`environment` niczego nie buduje ani nie migruje; istniejące zgody, propozycje i historia nie są usuwane.

Tablicę commituje Tide; profil zapisuje wyłącznie usługa.

`BoxStatus(slug)` zwraca `Builder.status` (`state`, `recipeHash`, `profileHash`, `diff`, `exists`, `usable`,
`nicFiltering`) oraz `nicLabel` („filtrowanie NIC: tak/nie”) i `cards[]` = `{card, boxWait}` dla kart projektu z dziennikiem,
`admission` (stan slotu, tylko odczyt) oraz `version` i `readState` opublikowanej migawki (K-98 #2).
Błędy: `bad-project`, `no-project`, `preparing` (przed pierwszą publikacją migawki).

#### Pole `boxWait`

Pole stanu karty (dziennik: zdarzenie `box.wait` z `value`, puste = wyczyszczone; `FlowState.box_wait`;
`derived.flow.boxWait` i `boxWaitLabel`; `BoxStatus.cards[].boxWait`). Jedyny czytnik: `cardflow/boxwait.py`,
`card_box_wait(card)` (przyjmuje `FlowState`, słownik karty albo `derived.flow`); #18 korzysta z niego, nie czyta
dziennika sam. Brak pola = nic nie blokuje.

| Wartość | Znacznik w panelu | Kto ustawia | Kto czyści |
|---|---|---|---|
| `environment` | „czeka na środowisko” | PT-172: „Tylko plan” oraz każde zatwierdzenie planu (`engine.approve_plan`, także `ApproveCardPlan` z okna decyzji i widoku planu) — plan nie omija pakietu zgód; PT-290: tylko przy `box.enabled: true`, a odroczona zgoda zamienia się w `environment` po ponownym włączeniu | PT-172: `ApproveCardEnvironment` bez `onlyPlan` przy włączonym pudełku |
| `start` | „czeka na pudełko” | #18 (silnik, PT-181): pudełko zatrzymane, trwa start | #18, gdy pudełko odpowiada |
| `rebuild` | „do przebudowy” | #18: hash przepisu niezgodny albo brak filtrowania NIC, przebudowa czeka na zatwierdzenie z planem | #18, gdy pudełko znów jest zgodne |

`set_box_wait(journal, card, value)` zapisuje zmianę (bez zdarzenia, gdy wartość i odroczenie się nie zmieniają;
czyszczenie kasuje także odroczenie PT-290). `environment_pending(card)` — karta czeka na środowisko albo ma
odroczoną zgodę; `require_environment(engine, card, source)` — D1 po zatwierdzeniu planu.

#### Pytanie o domenę (wynik kroku `denied`)

Wynik `{"result": "denied", "command": "network: <domena>"}` (odmowa proxy pudełka) nie jest zgodą na polecenie:
`step.finished` dostaje pole `network`, a decyzja karty (`kind: permission`) ma dwie możliwości bez polecanej:
`box-allow` („Dopisz do profilu karty i przebuduj”) i `deny` („Nie, wznów bez”). `box-allow` dopisuje domenę do
profilu karty (`profile.save_card`, domena czerwona jest odrzucana — o niej decyduje się w planie), dopisuje zdarzenie
`box.rebuild-requested` (dla silnika #18) i wznawia krok z informacją; `deny` wznawia krok z informacją o odmowie
i niczego nie zapisuje.

### Migracja `cardflow.json` do propozycji profilu (PT-190)

Przy starcie usługi `FlowEngine.migrate_environment()` (`box/migration.py`) dla każdego projektu z wpisem
w `networkDomains` albo `outsideCommands`, który nie ma `box.yaml` ani `box.proposed.yaml`, tworzy w Tablicy
`projects/<slug>/box.proposed.yaml`: `networkDomains[slug]` → `network.domains`, `outsideCommands[slug]` → `setup`
(polecenia `install`/`ci`/`restore`/`pull`…) albo `verify` (reszta; końcówka ` *` rodziny jest zdejmowana).
Domeny, których schemat profilu projektu nie przyjmuje (adres wewnętrzny, `*.com`), trafiają do `notes`. To tylko
propozycja do zatwierdzenia — nie jest to profil, nic nie jest zatwierdzane ani nadpisywane; stare klucze zostają
w `cardflow.json` (usunie je osobny ticket). Plik powstaje raz: usunięcie propozycji bez zatwierdzenia sprawi, że
następny start utworzy ją ponownie. Błąd zapisu w Tablicy nie zatrzymuje usługi.

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
- Rola `classify` (PT-42, domyślnie `qwen3:8b` na `default`) ocenia wątpliwe końcówki tur agentów (sekcja o stanie `waiting/reply`). Jako jedyna rola może mieć model chmurowy Ollamy (`…:cloud`, `…-cloud`), ale tylko z jawnym punktem (`"classify": "punkt:gpt-oss:120b-cloud"`); bez punktu rola jest niedostępna jak każda inna z modelem chmurowym.
- **Rola `classify` w widoku „Modele” i w zapisie** (PT-112, user story 11). Widok pokazuje ją obok `summary` i `describe` pod nazwą „Rozpoznawanie pytań”, z tym samym wyborem modelu, „Zaawansowanymi” (przypięcie do serwera) i `SetRole`; zapis i `Status().roles.classify` działają jak dla pozostałych ról (ten sam kod w `models/settings.py`, bez zmian w logice oceny). Stary `models.json` bez klucza `classify` daje wartość domyślną z `DEFAULT_ROLES` (`qwen3:8b` na `default`, lokalnie). Zmiana modelu roli działa bez restartu: ocena czyta `models.json` przy każdym wywołaniu, więc następna ocena idzie nowym modelem. Widok mówi jawnie: gdy rola nie ma modelu — „ocena wyłączona — liczą reguły”; gdy model jest w chmurze — znacznik „☁ w chmurze” (końcówka ostatniej wiadomości agenta wychodzi poza komputer). Lista modeli tej roli pokazuje też modele chmurowe (z „☁”), pozostałe role ich nie widzą. Wybór modelu w chmurze to jawne działanie użytkownika, a widok wysyła `SetRole` z serwerem roli (usługa bez jawnego serwera odmawia); żaden domyślny ani zapasowy wybór nie wskazuje chmury (decyzja 70).
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

## Pudełko

Odizolowane środowisko projektu w kontenerze Incusa (karta K-38, projekt: `docs/card-box-design.md`, decyzje
robocze: `docs/box-decisions.md`).

### Instalacja

Incusa, pulę, projekt i reguły sieci zakłada **wyłącznie** `scripts/box/install.sh` uruchamiany z sudo —
usługa działa jako zwykły użytkownik i niczego z tej listy nie zmienia. `make install` motywu nie woła żadnego
celu `box-*` (pilnuje tego test `scripts/tests/test_box_install.py`).

```bash
make box-state OUT=docs/box-prototype/host-before.txt   # migawka stanu hosta przed instalacją
make box-install                                         # sudo pyta raz o hasło; potem bez pytań
bash scripts/box/install.sh --dry-run                    # plan bez zmian (z sudo widzi cały stan Incusa)
make box-uninstall                                       # cofnięcie (pyta, usuwa wszystkie pudełka)
make box-state OUT=docs/box-prototype/host-after.txt
diff docs/box-prototype/host-before.txt docs/box-prototype/host-after.txt   # pusty
```

`make box-install` przyjmuje `BOX_PROXY_PORT=18080` (port proxy Tide, ta sama wartość co `box.proxy-port`
usługi; zmiana wymaga ponownego `make box-install`, bo reguły są statyczne) i `BOX_PROJECT_MODE=restricted|admin`.
Kroki — każdy najpierw sprawdza stan, więc drugie uruchomienie nic nie zmienia i wypisuje „bez zmian”:

1. **Symulacja apt** `apt-get -s install incus btrfs-progs` (plus `nftables`, gdy brak `nft`): odmowa, gdy lista
   zawiera pakiet z `qt6`/`kf6`/`libkf`/`plasma` w nazwie (skrypt wypisuje te pakiety) albo gdy apt podnosi lub
   usuwa zainstalowany pakiet — tego odinstalowanie by nie cofnęło. Pakiet `incus` z Ubuntu universe (6.0 LTS);
   repozytorium Zabbly tylko jako osobny wariant z osobną zgodą (P-7). Incusa zainstalowanego wcześniej nie przez
   ten skrypt nie przejmujemy.
2. **Instalacja** tych samych pakietów; lista nowych pakietów (z symulacji) trafia do pliku stanu
   `/var/lib/tide-box/install.state` przed instalacją, razem z grupami istniejącymi wcześniej i kopiami
   `/etc/subuid`, `/etc/subgid`.
3. **Preseed** `incus admin init --preseed`:
   - **pula** btrfs `tide` w rzadkim pliku `/home/tide-incus-pool.img` (200 GiB, decyzja D8). Incus zakłada plik
     pętli tylko w `/var/lib/incus/disks/tide.img`, więc skrypt robi tam dowiązanie do pliku na `/home` i po
     preseedzie sprawdza, że plik jest na `/home`; istniejącego pliku bez puli nie formatuje;
   - **mostek** `incusbr0`: IPv4 z NAT i DHCP (`ipv4.address: auto`), bez IPv6;
   - profil `default`: dysk na puli `tide`, karta `eth0` w `incusbr0`.
4. **Grupa**: użytkownik z `SUDO_USER` do grupy `incus` (dostęp przez `incus-user`, tylko do własnego projektu);
   działa po ponownym zalogowaniu (usługa `tide-daemon` też potrzebuje nowej sesji).
5. **Projekt ograniczony** `user-<uid>` — ten, którego używa `incus-user`; skrypt zakłada go sam, zanim zrobi to
   `incus-user` (ten mógłby dać projektowi własny mostek bez naszych reguł): `restricted=true`,
   `restricted.containers.nesting=allow` (Docker w pudełku), `restricted.devices.disk=allow` tylko pod katalogiem
   domowym (`restricted.devices.disk.paths`), `restricted.idmap.uid/gid` użytkownika (tylko pozwala instancji ustawić `raw.idmap`
   z tym uid/gid — mapowanie zakłada dopiero pudełko, PT-169), `restricted.networks.access=incusbr0`; własny profil `default` jak wyżej; `incus-user.socket`
   włączony. Do tego `root:<uid>:1` w `/etc/subuid` i `root:<gid>:1` w `/etc/subgid` z restartem `incus` —
   bez nich kontener z `raw.idmap "both <uid> <uid>"` nie startuje, a projekt ograniczony odrzuca `shift=true`
   na ścieżkach z katalogu domowego (PT-164, P-4); `make box-uninstall` usuwa te linie jak resztę dopisanych. `--project-mode admin` (grupa `incus-admin` = root hosta) skrypt przyjmuje tylko po próbie P-5
   z PT-164 i z wierszem `tryb-admin` w `docs/box-decisions.md`.
6. **nftables**: plik `/etc/tide-box/tide-box.nft` (treść: `scripts/box/files.sh rules <adres mostka> <port>`)
   sprawdzony `nft -c`, potem wczytany `nft -f`.
7. **Trwałość**: jednostka `tide-box-nft.service` wczytuje plik przy starcie (przed `network-pre.target`
   i Incusem); drop-in `/etc/systemd/system/incus.service.d/tide-box.conf` daje `incus.service` `Requires=` na nią
   — bez reguł Incus nie wstaje. **`systemctl stop` i `systemctl restart tide-box-nft` zatrzymują (restart:
   zatrzymują i uruchamiają ponownie) Incusa razem ze wszystkimi pudełkami i sesjami `incus exec`** —
   `Requires=` propaguje stop i restart; przy stop to zamierzone (pudełka gasną przed Incusem). Ponowne
   wczytanie reguł bez ruszania Incusa: `systemctl reload tide-box-nft` (`ExecReload=nft -f …`; reload nie
   przechodzi przez `Requires=`) albo wprost `sudo nft -f /etc/tide-box/tide-box.nft`. Jednostka nie ma
   `ExecStop`: jej zatrzymanie nie zdejmuje reguł (tabelę usuwa jawnie `uninstall.sh`), więc filtr zostaje
   w jądrze także wtedy, gdy incusd zostanie zabity po przekroczeniu czasu zatrzymania, a pudełka przeżyją.
   Nie używamy `nftables.service`:
   `/etc/nftables.conf` zaczyna od `flush ruleset` i skasowałby reguły Dockera i Incusa.

Na koniec skrypt wypisuje raport: nowe pakiety z symulacji, `incus list`, `incus project show user-<uid>`
i `nft list table inet tide-box`.

**Reguły sieci — decyzja „nftables statyczne, polityka w proxy”.** Reguły są statyczne i zakłada je tylko
skrypt z sudo; usługa nigdy nie modyfikuje nftables (bez sudoers i pomocnika z uprawnieniami, bez ACL sieciowych
Incusa). Polityka per pudełko — lista domen, Ollama hosta — żyje w proxy Tide, które jako jedyne jest osiągalne
z mostka. Podszycie się pudełka pod cudzy adres IP zatrzymuje filtrowanie NIC Incusa (PT-167), nie nftables.
Tabela `inet tide-box` ma dwa nazwane łańcuchy bazowe:

- `input` (hook input) i `forward` (hook forward), oba z priorytetem `filter + 10` — po łańcuchach tabeli
  `incus` (priorytet `filter`), więc `accept` Incusa (DNS i DHCP jego dnsmasq, forward z NAT) nie wystarcza,
  a `drop` tutaj jest ostateczny;
- łańcuchy mają politykę `accept`, bo hook widzi ruch wszystkich interfejsów hosta; „polityką drop” dla ruchu
  z `incusbr0` jest ostatnia reguła każdego łańcucha (`iifname "incusbr0" drop`);
- `input`: accept tylko `ip daddr <adres mostka> tcp dport <port proxy>` i DHCP (`udp sport 68 udp dport 67`);
  DNS (53, tcp i udp) jawnie DROP, potem cała reszta z mostka DROP;
- `forward`: wszystko z `incusbr0` i do `incusbr0` DROP — pudełko nie wychodzi nigdzie przez NAT, a połączenia
  z hosta do pudełka nie mają odpowiedzi (porty pudełka nie są wystawiane, sekcja 4.6 projektu).

**Konwencja `tide-proto` dla prób.** Prototypy (PT-164, PT-178) mogą na czas próby wstawić własne reguły
`accept` przed regułami drop: `nft insert rule inet tide-box forward iifname "incusbr0" … accept comment
"tide-proto"` (albo w łańcuchu `input`), a po próbie usunąć je po uchwytach (`nft -a list chain inet tide-box
forward`, `nft delete rule inet tide-box forward handle <n>`). Ponowne wczytanie pliku (`sudo systemctl reload
tide-box-nft` albo `sudo nft -f /etc/tide-box/tide-box.nft`) też je usuwa i nie dotyka pudełek. **Nie
`systemctl restart tide-box-nft`** — restart przez `Requires=` zatrzymuje Incusa ze wszystkimi kontenerami próby.
Usługa takich reguł nie zakłada.

**Odinstalowanie** (`make box-uninstall`, `scripts/box/uninstall.sh`, z `--dry-run` bez sudo) cofa kroki
w odwrotnej kolejności według pliku stanu: jednostka i drop-in, tabela `inet tide-box` i `/etc/tide-box`,
`incus admin shutdown` i zatrzymanie jednostek `incus*`, odmontowanie `/var/lib/incus`, odłączenie pętli puli,
usunięcie `incusbr0` i tabeli `inet incus`, `apt-get purge` pakietów z pliku stanu (najpierw symulacja; odmowa,
gdy purge usunąłby coś spoza listy), potem `/var/lib/incus`, `/var/cache/incus`, `/var/log/incus`, plik puli,
grupy założone przez pakiety, linie dopisane do `/etc/subuid` i `/etc/subgid` oraz plik stanu. Odmawia plików
jednostek bez znacznika Tide. Wszystkie pudełka i ich dane w puli znikają; katalogi hosta montowane do pudełek
(`~/.local/share/tide/box`) zostają.

**Dowód odwracalności:** `scripts/box/host-state.sh` (tylko odczyt; `nft list ruleset` przez sudo, gdy trzeba)
zapisuje pakiety (`dpkg-query -W`), łącza (`ip -o link`: nazwa i typ), `nft list ruleset` bez liczników,
`getent group`, `/etc/subuid` i `/etc/subgid`, istnienie pliku puli i katalogów Incusa oraz jednostki `incus*`,
`lxcfs*`, `tide-box*`. Migawka przed `make box-install` i po `make box-uninstall` musi być identyczna.

### Budowa pudełka (PT-167)

Moduły: `box/incus.py` (cienki adapter CLI), `box/builder.py` (budowa, kopia per karta, status, reset,
usunięcie, zatwierdzenie), `box/state.py` (dziennik, stany, `BoxStatus`, `card_box_name`), `box/settings.py`,
`box/cli.py`; punkt wejścia `python3 daemon/tide.py box …` (instalacja do PATH — z obrazem bazowym, #16).

**Adapter.** Każde polecenie `incus` dostaje `--project <projekt>` (domyślnie `user-<uid>`, projekt ograniczony
z instalacji) i stdin z `/dev/null`. Program: `$TIDE_INCUS_BIN` albo `incus` z PATH. Instancję szukamy po
dokładnej nazwie w `incus list --format json` (filtr `incus list <nazwa>` dopasowuje przedrostki — błąd z P-5).

**Ustawienia** — ta sama sekcja `box` w `$XDG_CONFIG_HOME/tide/cardflow.json` i ten sam moduł `box/settings.py`
(`BoxSettings`) co proxy (niżej, „Proxy wyjściowe”); klucze buildera, wszystkie opcjonalne: `project`
(`user-<uid>`), `base-image` (`tide-base`), `pool` (`tide`), `nic` (`eth0`), `ipv6-filtering` (`false`),
`agents-version`, `ip-wait-seconds` (30), `agent-uid`/`agent-gid` (1000 — użytkownik domu `/home/agent`;
ostateczną wartość ustala obraz bazowy #16). Zła wartość → ostrzeżenie na stderr `tide box` i wartość domyślna.
Adresu proxy w ustawieniach nie ma: builder bierze adres i port z **uruchomionego proxy** (`box.proxy.running()`:
nasłuch na adresie mostka `box.bridge`, port `box.proxy-port`) — jedno źródło dla proxy i budowy.

**Kolejność budowy** (`tide box build <slug>`, profil projektu z Tablicy):

Gdy `verify` sprawdza kod projektu (np. `npm ci`, potem `npm run lint`), użyj
`tide box build <slug> --verify-worktree <katalog>`. CLI sprawdza przynależność katalogu do repozytorium
projektu w Tablicy i brak zmian śledzonych plików. Przesyła archiwum śledzonych plików HEAD, bez `.git`,
plików nieśledzonych i ignorowanych zależności hosta. Builder rozpakowuje je w `/opt/tide/verify/work`
i uruchamia tam wyłącznie `verify` jako użytkownik agenta z jego UID/GID i `HOME=/home/agent`;
pierwsze niepowodzenie zatrzymuje następne polecenia tej weryfikacji.
Kod commita zapisuje w `box.verify.source_commit`. Kopia i jej zależności są usuwane przed migawką, także
po nieudanym verify; błąd sprzątania przerywa budowę. Nie powstaje montowanie repozytorium hosta.
Polecenia przygotowania zależności muszą być jawnie zatwierdzone w `verify` profilu. `setup` zachowuje
dotychczasowy katalog pracy. Bez opcji zachowanie buildera pozostaje dotychczasowe.

1. `incus init <base> <slug>` — kopia obrazu bazowego (to `launch` bez startu: karta sieciowa dostaje filtrowanie,
   zanim wyśle pierwszy pakiet) z `-c limits.cpu=… -c limits.memory=…` z profilu
   (domyślnie 8 / 24GiB). Docker (`docker.enabled: true` albo `docker` w `stacks`) dostaje
   `security.nesting=true`, `security.syscalls.intercept.mknod=true` i
   `security.syscalls.intercept.setxattr=true`, przed startem i pierwszym szablonem.
   Bez Dockera te klucze są pomijane. **Nigdy `security.privileged`.**
   `incus config device override <slug> root size=<resources.disk>` (domyślnie 60GB); prywatny katalog hosta
   `~/.local/share/tide/box/<slug>/home` (0700) jako urządzenie dyskowe na `/home/agent`.
   `raw.idmap` mapuje UID/GID użytkownika hosta na `agent-uid/gid`, tak samo jak BoxRunner.
   Dawny wolumen jest wcześniej migrowany bez usuwania danych (PT-189, niżej).
2. Filtrowanie NIC (niżej).
3. `incus start`, adres IPv4 pudełka, `set_box_policy(ip, build_domains(profile), host-ollama, dziennik odmów
   budowy)` w proxy Tide (#5) — przed pierwszym `incus exec`. Dziennik odmów:
   `$XDG_STATE_HOME/tide/box/<slug>/build-denied.log`.
4. Kontrola braku swapu: pierwszy `incus exec` sprawdza `/sys/fs/cgroup/memory.swap.max=0`.
   Brak pliku (wymagane cgroup v2), błąd odczytu albo inna wartość przerywa budowę przed szablonami i migawką.
   Następnie szablony stosów wg `stacks`: `incus file push box/stacks/lib.sh` i `box/stacks/<stos>.sh` do
   `<slug>/opt/tide/stacks/`, potem `bash /opt/tide/stacks/<stos>.sh` w kolejności `stacks` (niżej, „Szablony stosów
   w pudełku (PT-192)”; brak skryptu albo `lib.sh` = błąd przed `incus`).
5. `apt-get update` i `apt-get install -y --no-install-recommends` z pakietami szablonów i profilu
   (`templates.build_apt`). 6. Polecenia `setup` (`bash -c`). 7. `docker pull` obrazów (`templates.build_images`),
   tylko przy włączonym Dockerze. Nieudany pull nie przerywa pozostałych pobrań ani budowy: lista
   `failed_images` w `box.ready` i stan „zbudowane z ostrzeżeniem”.
   8. `verify` — nieudane polecenie nie przerywa budowy: zdarzenie `box.verify`
   z `result: fail` i listą `failed`, stan „zbudowane z ostrzeżeniem” (`clean` z `warning`).
9. Klucz `user.tide.recipe` = odcisk `profile.recipe_stamp(profile, templates_version, agents-version)`
   (JSON: hash i skróty kluczy do `diff`), potem `incus snapshot create <slug> clean --reuse`. Migawka niesie
   klucz w swojej konfiguracji, więc każda kopia z `<slug>/clean` ma ten sam odcisk. (Incus 6.0 nie ustawia
   opisu migawki przy `snapshot create`, dlatego „opis” to klucz `user.*` konfiguracji migawki.)
   `templates_version` to skrót wszystkich `box/stacks/*.yaml` i `*.sh` (zapisany jako `builder.STACKS_VERSION`).

### Docker i pula portów w kroku (PT-171)

`docker.enabled: true` włącza także stos `docker`; sam stos `docker` włącza Docker niezależnie od
pominiętej lub fałszywej flagi. Normalizacja profilu służy też do hasha przepisu.
Szablon `box/stacks/docker.sh` należy do PT-192 (#29): instaluje Docker, dodaje `agent` do grupy `docker`
i zapisuje drop-in `dockerd` z proxy oraz `NO_PROXY=BOX_NO_PROXY`. PT-171 nie zmienia tego skryptu ani proxy.
Builder ustawia konfigurację Incusa i przez `incus exec` pobiera bez duplikatów obrazy szablonów
(w tym przypięty Ryuk z `docker.yaml`) oraz `docker.images` profilu, z `HTTPS_PROXY` budowy.
Nie uruchamia Dockera hosta. Budowa nie przekazuje `DOCKER_HOST`; BoxRunner usuwa tę zmienną również
z dodatków środowiska kroku. Testcontainers korzystają z gniazda `/var/run/docker.sock` wewnątrz pudełka.

`box/ports.py` rezerwuje 10 kolejnych portów z `ports.pool` (obie granice włącznie). Każdy następny
zakres zaczyna się 10 portów dalej. Niepełny ogon puli nie jest przydzielany. `PORT`, `E2E_PORT`,
`TIDE_PORT_BASE` i wszystkie nazwy z `ports.env` mają wartość pierwszego portu zakresu.
Profil karty zastępuje pulę projektu i sumuje nazwy zgodnie z `profile.merge`. Bez `ports.pool` nie ma
automatycznego przydziału. Rezerwacja nie wymusza na aplikacji użycia portów; aplikacja musi czytać środowisko.

Stan puli: `$XDG_STATE_HOME/tide/box/<slug>/ports/<pudełko>.json`, blokada `flock` na oddzielnym
pliku `.lock`, atomowa wymiana stanu. Blokada działa między procesami. Przydziały różnych kart i kroków
w jednym pudełku nie nachodzą na siebie, również po zmianie granic puli. Osobne pudełka mają osobne pule,
bo mają oddzielne przestrzenie sieciowe. Pliki stanu leżą poza montowaniami agenta.

Silnik zapisuje zamiar `box.ports` przed przydziałem i `boxPortOwner` w metadanych kroku.
Wyczerpana pula oznacza `step.waiting` z przyczyną `box`; kolejne tyknięcie ponawia przydział.
Wartości portów BoxRunner zapisuje tylko w `in/env`, nigdy w argv ani w środowisku klienta Incusa hosta.
Zakończenie kroku (także `error`) i nieudany start zwalniają przydział; odzyskiwanie po restarcie zwalnia
również zakończone kroki, nieudane zamiary startu i rezerwacje sprzed zamiaru startu.
Błąd utrwalenia udanego startu pozostawia rezerwację przy otwartym zamiarze; odzyskiwanie
odnajduje jednostkę i zachowuje jej porty do końca kroku.
Tryb `BoxRunner --demo` korzysta z tej samej puli zatwierdzonego profilu. Zwalnia przydział dopiero
po końcu klienta i potwierdzeniu `inactive` jednostki w pudełku. Przerwanie oczekiwania wywołuje zatrzymanie jednostki;
stan aktywny lub nieznany pozostawia trwałą rezerwację. Kolejne demo sprawdza osierocone przydziały
tego pudełka i zwalnia wyłącznie te z potwierdzonym końcem jednostki. Identyfikator właściciela
`demo-<czas>-<losowy sufiks>` określa nazwę jednostki; osobna blokada demo jest dziedziczona
przez klienta Incusa (`pass_fds`) i chroni również okno przed utworzeniem jednostki. Śmierć rodzica
lub zamknięcie jego deskryptora nie zwalnia blokady żywego klienta. Dopiero zamknięcie ostatniej
kopii deskryptora pozwala kolejnemu demo sprawdzić jednostkę i bezpiecznie odzyskać rezerwację.

**Ryzyko K1.** Grupa `docker` daje użytkownikowi `agent` prawa roota pudełka, w tym możliwość
zamontowania rootfs. Wewnętrzne `denyRead` nie chroni poświadczeń agenta przed kodem testów.
Filtrowanie NIC jest po stronie Incusa, pełna ochrona poświadczeń wymaga K3. Ten opis jest widoczny
w propozycji profilu i w pakiecie zgód; pozycja Docker pozostaje żółta.

**Dalszy zakres, poza PT-171 (decyzja użytkownika 6.11.2026).** Przypisywanie rzeczywistych sesji
Testcontainers do kart oraz sprzątanie ich kontenerów i obrazów po domknięciu wymagają osobnego ticketu.
PT-171 nie wykonuje `docker rm` ani `docker image prune`. Szkic wymagań:
`docs/agents/plans/followups/pt-171-testcontainers-cleanup.md`.

**Swap w projekcie ograniczonym (PT-192).** W Incusie 6.0 jawne `limits.memory.swap=false` jest zabronionym
kluczem niskiego poziomu. Builder pomija ten klucz: przy twardym `limits.memory` Incus ustawia limit swapu na 0
również dla wartości domyślnej. Kontrola z punktu 4 potwierdza efekt w jądrze; budowa odmawia, jeśli nie może
go potwierdzić. `restricted.containers.lowlevel` pozostaje zablokowane. Uzasadnienie i źródła:
decyzja `swap-zero-bez-lowlevel` w `docs/box-decisions.md`. Cykl życia (#19/PT-182) powinien zachować tę samą
zasadę przy ponownym nakładaniu limitów pamięci.

Każde `incus exec` budowy: `-T`, jako root pudełka, z `HTTPS_PROXY`/`HTTP_PROXY` (i małymi literami)
`http://<adres mostka>:<box.proxy-port>` z uruchomionego proxy, `NO_PROXY` = `box/netenv.py` `BOX_NO_PROXY`,
`DEBIAN_FRONTEND=noninteractive`, `TIDE_BOX_PROXY` (adres proxy) i `TIDE_BOX_NO_PROXY` (= `BOX_NO_PROXY`) dla
szablonów, `TIDE_NODE_VERSION`/`TIDE_DOTNET_SDK` z profilu. **Brak uruchomionego proxy (albo jego adresu) to błąd
przed pierwszym poleceniem `incus`** (polityka `ProxyMissing`) — budowy „bez proxy” nie ma. `tide box build`,
`tide box base` i `tide box login` z wiersza poleceń używają proxy uruchomionego w tym procesie, a bez niego startują
własne `ProxyService` na czas polecenia (decyzja `base-wlasne-proxy-cli`, dla `build` od PT-192); przy działającej
usłudze Tide port jest zajęty i polecenie odmawia (kod 1). Dziennik odmów budowy jest czyszczony na jej starcie
(pusty po budowie = żadna domena nie brakowała), a `tide box build` wypisuje jego ścieżkę. Po migawce (i przy błędzie) builder zdejmuje politykę budowy
(`clear_box_policy`), więc pudełko po budowie nie ma w proxy żadnej polityki do czasu kroku (#18).
Wyjście poleceń trafia na bieżąco (strumieniowo) do `$XDG_STATE_HOME/tide/box/<slug>/build.log`.
`type: vm` → błąd „nieobsługiwane w tej wersji” bez żadnego wywołania `incus`. Istniejące pudełko jest przy
budowie usuwane i tworzone od nowa (dom `/home/agent` zostaje); budowa odmawia, gdy w pudełku biegną kroki.

**Filtrowanie NIC — dlaczego.** Proxy Tide (#5) rozpoznaje pudełko po adresie IP, a agent w grupie `docker`
ma w pudełku prawa roota i mógłby zmienić adres IP albo MAC, żeby dostać politykę innego pudełka. Na karcie
pudełka builder ustawia `security.mac_filtering=true`, `security.ipv4_filtering=true` i
`security.ipv6_filtering=<ipv6-filtering>` — Incus odrzuca pakiety ze źródłowym adresem innym niż przydzielony
(P-5: podszycie nie dochodzi do hosta). `ipv6_filtering` domyślnie `false` (decyzja `filtrowanie-nic-bez-ipv6`:
Incus 6.0 nie startuje karty z nim bez `br_netfilter`, a mostek nie ma IPv6). Karta z profilu `default` dostaje
`config device override`, karta lokalna (kopia per karta) — `config device set`. Dzieje się to na każdej ścieżce
tworzącej pudełko: przy budowie i przy kopii. `tide box status` pokazuje „filtrowanie NIC: tak/nie” z
`incus config device show`; „nie” (brak `mac`/`ipv4`) = stan **niezgodne** i `BoxStatus.usable == False` — krok
nie startuje (#18).

**Kopia per karta** (`box: per-card`): `copy_for_card(slug, card)` → `incus copy <slug>/clean <slug>-<karta>`
(nazwa wyłącznie z `card_box_name(slug, card)`, np. `plasma-tide-k-38`), `user.tide.copy-of=<slug>`,
`user.tide.card=K-38`, własny katalog `<slug>/cards/k-38/home` na `/home/agent` (0700, to samo mapowanie UID/GID),
filtrowanie NIC (`device set`), **własna migawka `clean`** kopii (`incus copy` z migawki daje instancję bez
migawek; migawka po podpięciu domu i filtrowania, więc `reset --card` ich nie cofa) i zdarzenie `box.ready`
z `card`, `copy_of`, `hash` i `warning` przeniesionym z pudełka projektu (zbudowane z ostrzeżeniem → kopia też).
Druga kopia dla tej samej karty nie powstaje (zwraca istniejącą nazwę), chyba że przepis projektu został
przebudowany z nowym hashem — wtedy nieaktywna kopia zostaje odtworzona z nowej migawki.
Przed odtworzeniem kopii jej dawny dom jest migrowany bez usuwania danych. Nowa kopia pozostaje zatrzymana;
start do pracy prowadzi #18/#19.

**Dziennik i stany.** `<Tablica>/projects/<slug>/box/journal.jsonl` (JSON Lines, `flock`): `box.build`
(`phase: start|end`, `result`, `steps`, `hash`), `box.ready`, `box.verify` (`result`, `failed`), `box.reset`,
`box.stop`, `box.approved` (`source: cli`, `accepted_red`), `box.use`/`box.release` (krok w pudełku — woła
#18 przez `mark_in_use`/`mark_released`) i `box.destroy`. `box.build` (koniec), `box.ready`, `box.reset`,
`box.stop`, `box.verify` mają `duration` w sekundach. Stany: `brak → budowa → clean → w użyciu → nieaktualny → reset →
zatrzymane`, plus `niezgodne` i `błąd budowy`. Stan z dziennika jest korygowany przez Incusa: brak instancji →
`brak`, brak filtrowania → `niezgodne`, hash migawki różny od profilu → `nieaktualny (do przebudowy)` z listą
kluczy z `profile.diff` (np. `stacks, apt`; zmiana zasobów albo domen nie jest różnicą przepisu), zatrzymana
instancja → `zatrzymane`. `BoxStatus.usable` (czy krok może wejść, #18) wymaga istniejącej instancji,
filtrowania NIC, **zatwierdzonego profilu** (brak profilu = nieużywalne) i braku różnic przepisu.

**CLI** `python3 daemon/tide.py box …` (kody: 0 sukces, 1 błąd, 2 zły argument, 3 odmowa/niezgodne/nieaktualne):

**K-38 v1, PT-267:** obecnie działają tylko `status` i `stop`, przez usługę D-Bus.
Pozostałe polecenia z poniższej historycznej listy oraz modułowy runner `--demo` są wyłączone do etapów II/III;
nie wykonują operacji lokalnie. Zachowany klient i kontrakt usługi: [CLI v1](box-cli-v1.md).

| Polecenie | Działanie |
| --- | --- |
| `build <slug>` | budowa jak wyżej |
| `status <slug> [--card K-38] [--json]` | stan, stan w dzienniku, hash migawki i profilu, różnice, filtrowanie NIC, kroki w pudełku |
| `reset <slug> [--card K-38]` | `incus snapshot restore <pudełko> clean`; odmawia (kod 3), gdy biegnie krok; dom hosta zostaje, a stara migawka dostaje ponownie właściwe montowanie |
| `stop <slug> [--card K-38]` | zatrzymanie przez `incus stop`; kod 3, gdy biegnie krok; zdarzenie `box.stop` z czasem trwania |
| `start <slug> [--card K-38]` | odczyt limitów z profilu, `incus start`, oczekiwanie na `incus exec … true`; zdarzenie `box.ready` z czasem trwania |
| `destroy <slug> --yes` | usuwa instancje projektu i kopii (`user.tide.copy-of`); zachowuje domy, eksporty i stare wolumeny do osobnego sprzątania; odmawia, gdy biegnie krok |
| `approve <slug> --file box.yaml [--accept-red POZYCJA]…` | walidacja schematem z #2, zapis w Tablicy i `box.approved source=cli` — jawna zgoda z wiersza poleceń, równoważna pakietowi zgód; pozycja czerwona (np. `github.com`) tylko z `--accept-red` |
| `base` | buduje obraz bazowy Tide (`make box-base`) — niżej, „Obraz bazowy Tide” |
| `login [<slug>]` | logowanie agentów bez K3 (K1) w pudełku projektu; przy K3 dla obu programów — „nic do zalogowania” |

`run` doda #28. Tekstowy `status` pokazuje także czasy zdarzeń `box.stop` i `box.reset`; kontrakt JSON
`BoxStatus` pozostaje bez zmian (K-36).

**Cykl życia (PT-182, D14).** `box/lifecycle.py` obsługuje auto-stop po `box.idle-hours` bez kroku
(ustawienie usługi w sekcji `box` pliku `cardflow.json`, domyślnie 4 godziny). Sprawdzenie biegnie w `tick`
silnika przy włączonym `box.enabled`. Liczy się ostatnie użycie/zwolnienie/start pudełka; sam stary czas
zdarzenia nie pozwala zatrzymać pudełka. Rezerwacje `box.use`, `procs` i stan jednostek
`systemctl --user is-active` wewnątrz pudełka blokują stop, reset i przebudowę. Po restarcie usługi stan
jednostek pochodzi z trwałych wpisów kroków; nieznany stan również blokuje operację. CLI sprawdza te same
dzienniki i jednostki. Krok trwający dłużej niż limit bezczynności nie zostaje zatrzymany.
Kontrola aktywności i cała operacja destrukcyjna biegną pod wspólną blokadą projektu (`flock`),
używaną również przy rezerwacji kroku. Przed `box.use` krok ponownie sprawdza zgodność i gotowość
pudełka pod tą blokadą. Blokada obejmuje też przebudowę, reset, kopiowanie i usuwanie kopii.

Przy każdym starcie istniejącego pudełka limity są odświeżane przez `incus config set`: `limits.cpu`
z `resources.cpu`, `limits.memory` z `resources.memory`. Dotyczy to też restartu na nowe montowania:
wywołujący odświeża limity przed `ensure_devices` i sprawdza swap po przygotowaniu montowań.
Zgodnie z regułą PT-192 `swap-zero-bez-lowlevel` nie wysyłamy zabronionego `limits.memory.swap`,
także false; twardy limit RAM ma dawać `memory.swap.max=0` w cgroup v2, co sprawdzamy po starcie
oraz przed uruchomieniem agenta. Brak potwierdzenia odmawia wykonania kroku. `resources.swap=true`
jest jawnie odrzucane w ograniczonym projekcie. Zmiana zasobów nie zmienia hasha przepisu i nie wymaga
przebudowy. Start nie usuwa ani nie wyłącza filtrowania NIC. Instalator ustawia
`restricted.snapshots=allow` także przy aktualizacji projektu, zachowując pozostałe ograniczenia.

Pudełko projektu resetuje się na żądanie użytkownika lub przy jawnej przebudowie zmienionego przepisu
(`box.reset`, powód `zmiana przepisu`). Bezczynność i domknięcie karty nie resetują pudełka projektu.
Po `card.closed` usługa usuwa wyłącznie kopię karty wyliczoną przez `card_box_name` i potwierdzoną
metadanymi `user.tide.copy-of` oraz `user.tide.card`. Przed usunięciem migruje dawny dom;
katalog hosta, eksport i stary wolumen pozostają do osobnego sprzątania. Zapisuje `box.reset`
z powodem `karta domknięta`, `removed: true` i `duration`. Gdy kopia ma aktywny krok, zapisuje
`result: deferred` i ponawia sprzątanie w kolejnych przebiegach, także po restarcie usługi.

**Testy bez Incusa.** `daemon/tests/fakebin.py` ma atrapę `incus` (`daemon/tests/fake_incus.py`): zapisuje argv
w `$TIDE_FAKE_LOG`, trzyma stan w `$TIDE_FAKE_INCUS_STATE` (`list`, `snapshot list`, `config show`,
`config device show`, `info` wypisują z niego JSON; `init/launch/copy/snapshot/restore/start/stop/delete/config
set/config device set|override|add` i wolumeny go zmieniają), a `exec` wykonuje polecenie lokalnie (w `--cwd`)
z PATH zaczynającym się od `<fakebin>/box` — `apt-get`, `docker` i inne programy „w pudełku” tylko zapisują
wywołanie. `daemon/tests/__init__.py` ustawia `TIDE_INCUS_BIN` na nieistniejący program, więc żaden test nie
dosięgnie prawdziwego Incusa. Testy: `daemon/tests/test_box_builder.py`.

### Obraz bazowy Tide (PT-179)

Obraz, z którego `incus init` buildera robi każde pudełko projektu (`box.base-image`, domyślnie `tide-base`).
Budowa raz na host i po każdej zmianie wersji agentów albo skryptów: `make box-base` (= `python3 daemon/tide.py box
base`, bez sudo — projekt ograniczony Incusa). Moduły: `box/base.py` (budowa i logowanie), `box/stacks/base.sh`
(skrypt w pudełku), `box/stacks/tide-step.sh`, `box/agents.py` (wersje), `box/netenv.py` (`BOX_NO_PROXY`).

**Kolejność `tide box base`:**

1. Adres proxy Tide (#5) — przed pierwszym poleceniem `incus`. Polecenie z wiersza poleceń używa proxy uruchomionego
   w tym procesie, a gdy go nie ma, startuje własną instancję tego samego `ProxyService` (`start_service` z ustawień:
   adres mostka, `box.proxy-port` — jedyny port wpuszczany przez nftables) i zatrzymuje ją po budowie. Gdy port
   trzyma już usługa Tide (`box.enabled`), start się nie uda — budowa odmawia z tą informacją (zatrzymaj usługę na
   czas budowy). Brak mostka albo `incus` → odmowa. Ścieżki „bez proxy” nie ma.
2. `incus init images:ubuntu/24.04 tide-base` (bez startu), filtrowanie NIC jak w builderze, start, adres IPv4.
3. `set_box_policy(ip, domeny szablonu base, ollama=False, base-denied.log)` — lista budowy = `templates.build_domains({})`,
   czyli szablon `base` z domenami instalatorów agentów z P-6 (`claude.ai`, `downloads.claude.ai`, `registry.npmjs.org`,
   `archive.ubuntu.com`, `security.ubuntu.com`, `nodejs.org`). Dziennik odmów
   `$XDG_STATE_HOME/tide/box/tide-base/base-denied.log` (0600) jest czyszczony na starcie budowy — pusty po udanej
   budowie znaczy „żadna domena nie brakowała”; wyjście poleceń w `base-build.log` obok.
4. `incus file push` skryptów do `/opt/tide/stacks/` i czcionek użytkownika: zwykłe pliki czcionek (`.ttf`, `.otf`,
   `.ttc`, `.woff2`, …) z `$XDG_DATA_HOME/fonts` (`~/.local/share/fonts`) hosta, bez dowiązań, jako `fonts.tar`
   (właściciel root, 0644) — kopiowane, nie montowane (K-33).
5. `bash /opt/tide/stacks/base.sh <krok>` jako root pudełka, każdy krok osobnym `incus exec` z proxy w środowisku
   i wartościami z usługi: `TIDE_BOX_PROXY`, `TIDE_BOX_NO_PROXY` (= `BOX_NO_PROXY`), `TIDE_AGENT_UID/GID`
   (`box.agent-uid/gid`), `TIDE_CLAUDE_VERSION`, `TIDE_CODEX_VERSION`. Kroki (`base.STEPS`):

   | Krok | Co robi |
   | --- | --- |
   | `sources` | źródła Ubuntu (`ubuntu.sources` albo `sources.list`) z `http://` na `https://` (decyzja `apt-przez-https`; obce źródło `http://` = błąd), `/etc/apt/apt.conf.d/90tide-proxy` z `Acquire::http::Proxy` i `Acquire::https::Proxy` |
   | `packages` | `apt-get update --error-on=any`, `git build-essential curl ca-certificates xvfb weston fontconfig nodejs npm bubblewrap socat apparmor` |
   | `user` | `agent` z uid/gid 1000 (`ubuntu` z uid 1000 jest przejmowany), lingering (`loginctl enable-linger`) |
   | `environment` | `/etc/environment` (`HTTP_PROXY`, `HTTPS_PROXY`, małe litery, `NO_PROXY`/`no_proxy` = `BOX_NO_PROXY`, `DISABLE_AUTOUPDATER=1`; reszta pliku, np. `PATH`, zostaje), `/etc/profile.d/tide-proxy.sh` (powłoki logowania) i `/etc/systemd/user.conf.d/90-tide-proxy.conf` (`DefaultEnvironment` usług `systemd --user` — kroki) |
   | `fonts` | `fonts.tar` do `/usr/local/share/fonts/tide`, `fc-cache` |
   | `tide-step` | `/usr/local/bin/tide-step` (niżej) |
   | `apparmor` | profil `bwrap` (ten sam co `scripts/sandbox/check.py profile` na hoście) w `/etc/apparmor.d/bwrap-userns-restrict`, `apparmor_parser -r`, usługa `apparmor` włączona — wczytuje go przy każdym starcie pudełka (decyzja `bwrap-profil-w-pudelku`) |
   | `claude` | instalator natywny `https://claude.ai/install.sh` pobrany do pliku (bez potoku do powłoki) i uruchomiony z dokładną wersją w katalogu tymczasowym; program do `/usr/local/bin/claude` (poza `/home/agent`, który w pudełku projektu przykrywa dom hosta); `claude --version` jako `agent` |
   | `codex` | `npm install -g @openai/codex@<wersja>`; `codex --version` jako `agent` |
   | `cleanup` | `apt-get clean`, pamięć npm, listy apt, `/opt/tide` |

6. `user.tide.agents` = `agents.AGENTS_VERSION`, migawka **`tide-base/clean`**, `incus publish tide-base/clean --alias
   tide-base --reuse` (obraz dla `incus init` buildera), `incus stop tide-base`.
7. Polityka budowy zdjęta z proxy (`clear_box_policy`) — także po błędzie. Nieudany krok = błąd bez migawki i obrazu.

Bez drop-inu `dockerd` (szablon `docker.sh`, PT-192 — niżej). Skrypt nie wpisuje żadnej wartości z usługi (adres proxy,
`NO_PROXY`, wersje): odmawia pracy bez `TIDE_BOX_PROXY` równego `HTTP(S)_PROXY` wywołania, z nieprzypiętą wersją
(`x.y.z`) albo poza rootem.

**Wersje agentów** (`box/agents.py`): Claude Code `CLAUDE_VERSION` (wersja hosta z analizy 4.7, sprawdzona w P-6),
Codex `CODEX_VERSION` (P-3/P-6) — dokładne, nigdy `latest`; auto-aktualizacja Claude Code w pudełku wyłączona.
`AGENTS_VERSION` wchodzi do hasha przepisu pudełek projektów (`Builder.agents_version`; `box.agents-version`
w ustawieniach go nadpisuje), a skrypty `box/stacks/*.sh` — przez `templates_version`. Podbicie wersji albo zmiana
`base.sh` robi pudełka projektów nieaktualnymi: `make box-base`, potem `tide box build <slug>`. Podbicie Codexa
= powtórka P-3 (dostawca K3 zależy od ścieżek `backend-api/codex`).

**`BOX_NO_PROXY`** (`box/netenv.py`) = `localhost,127.0.0.1,::1,172.16.0.0/12,host.docker.internal` (P-6, „NO_PROXY
per klient”): pętla zwrotna pudełka i sieci Dockera w pudełku. Ta sama stała idzie do `/etc/environment` obrazu,
do każdego `incus exec` budowy (#4) i — w #6 — do kroku. `netenv.proxy_env(proxy)` daje komplet zmiennych.

**`tide-step`** (`/usr/local/bin/tide-step`, kontrakt BoxRunnera #6/PT-169, który może go rozszerzyć):
`tide-step <plik env> <plik kodu> <program> [argumenty…]` — wczytuje `/etc/environment`, potem plik kroku
(`KLUCZ=wartość`, jako dane — nic nie jest wykonywane; zły wiersz → kod 2), uruchamia program i zapisuje jego kod
wyjścia atomowo do `<plik kodu>` (0600), jak `EXIT_WRAPPER` kroku na hoście. Krok biegnie jako usługa
`systemd --user` agenta (decyzja `krok-jako-usluga-user`), więc zmienne kroku nie mogą przyjść przez środowisko
wywołującego.

**Logowanie agentów (K1) — `tide box login [<slug>]`.** Poza budową, jednorazowo. Programy do zalogowania to
`k3.login_programs()`: dziś puste (K3 dla Claude Code i Codexa), więc polecenie kończy się „nic do zalogowania”
(kod 0), a `tide box base` — „logowanie agentów niepotrzebne”. Gdyby K3 dla programu zostało wyłączone, budowa
kończy się instrukcją „zaloguj: `tide box login` <projekt>” (kod 0, nie błąd), a `tide box login <slug>` otwiera
`incus exec -t <pudełko> --user 1000 --cwd /home/agent -- claude login` (i `codex login`) z proxy Tide i polityką
projektu (`compose_policy`) na czas logowania. Poświadczenia z `~/.claude`/`~/.codex` hosta nigdy nie są kopiowane.

**Co jest trwałe.** `/home/agent` pudełka projektu to katalog hosta `<slug>/home` (PT-189) — przeżywa reset
z migawki `clean` i przebudowę pudełka (logowanie K1, `~/.npm`, `~/.nuget/packages`, `~/.cache/ms-playwright`);
pozostaje również po `tide box destroy` do osobnego sprzątania. Obraz bazowy (instancja `tide-base` z migawką `clean` i obraz `tide-base`) żyje do
następnego `make box-base`; reszta systemu plików pudełka wraca do `clean` przy resecie. Programy agentów,
czcionki i konfiguracja proxy są w obrazie, nie w `/home/agent`.

Testy: `daemon/tests/test_box_base.py` (atrapa `incus` z `fake_incus.py` uczy się `publish`; prawdziwy `base.sh`
nie jest wykonywany w całości — jego funkcje plików biegną po `source` w katalogu tymczasowym).

### Szablony stosów w pudełku (PT-192)

Skrypty powłoki `box/stacks/<stos>.sh` (`node`, `playwright`, `dotnet`, `docker`, `python-qt`) obok danych
`<stos>.yaml` (#30). Builder (krok 4 budowy) wypycha `lib.sh` i skrypty z `stacks` profilu do `/opt/tide/stacks/`
i uruchamia każdy jako root pudełka, w kolejności `stacks`, na obrazie bazowym. Każdy skrypt: `set -euo pipefail`,
`source lib.sh`, odmowa poza rootem i bez proxy (`TIDE_BOX_PROXY` musi być równe `HTTP(S)_PROXY` wywołania),
pobieranie wyłącznie funkcją `fetch` (`curl` do pliku, tylko `https://`, także przekierowania — nigdy potok do
powłoki) z domen własnego szablonu, wersje przypięte w skrypcie albo wzięte z profilu (nigdy `latest`). Żaden
skrypt nie czyta ani nie kopiuje plików agentów hosta (`~/.claude`, `~/.codex`).

**Kto jest właścicielem czego.** `base.sh` (obraz bazowy, apt przez HTTPS, `/etc/environment` z proxy) — #16/PT-179;
skrypty stosów, `lib.sh`, **drop-in `dockerd` i `/etc/docker/daemon.json` — wyłącznie `docker.sh`** (#29/PT-192;
test `test_box_stacks.py` sprawdza, że żaden inny plik repozytorium poza dokumentacją, testami i jednorazowymi
skryptami prototypu nie tworzy `docker.service.d`); obrazy Dockera (`docker pull` z `templates.build_images`) —
builder (#4) i konfiguracja Dockera w pudełku (#8/PT-171), nie szablon; domeny i klasy ryzyka — `<stos>.yaml` (#30).

| Szablon | Co robi | Wejście z profilu |
| --- | --- | --- |
| `node.sh` | Node z `nodejs.org/dist` (archiwum `.tar.gz` ze sprawdzoną sumą z `SHASUMS256.txt` tej wersji) do `/usr/local` (wygrywa w PATH z Node 18 z apt obrazu, którego potrzebuje Codex); npm z tej dystrybucji; `CHROME_BIN=/usr/local/bin/tide-chrome` dla Karmy (opakowanie uruchamiające Chromium z `/opt/ms-playwright`, bez niego kod 127 z podpowiedzią „dodaj stos playwright”); `NODE_USE_ENV_PROXY=1` (Node ≥ 24 `fetch` przez proxy, P-6) | `node.version`: dokładne `x.y.z` albo numer główny przypięty w `NODE_PINS` (18, 20, 22, 24); bez wersji — `NODE_DEFAULT_MAJOR` (22); `x.y` = błąd |
| `playwright.sh` | `npx --yes playwright@<PLAYWRIGHT_VERSION> install --with-deps chromium` przez proxy (zależności systemowe przez apt obrazu), przeglądarki w `/opt/ms-playwright` (w migawce `clean`, właściciel `agent` — projekt z inną wersją Playwrighta dociąga w kroku swoją rewizję), `PLAYWRIGHT_BROWSERS_PATH`; wymaga Node ≥ 20, czyli stosu `node` wcześniej w `stacks` | — (wersja przypięta w skrypcie) |
| `dotnet.sh` | `dotnet-install.sh` z `dot.net` pobrany do pliku, SDK do `/usr/share/dotnet` (dowiązanie `/usr/local/bin/dotnet`), zależności (`libicu74`…) z apt, `DOTNET_ROOT`, telemetria wyłączona; `~/.nuget/NuGet/NuGet.Config` agenta z `<clear />` i **wyłącznie `nuget.org`** — bez rejestrów wewnętrznych (zapis jako `agent`, bo `/home/agent` to wolumen agenta) | `dotnet.sdk`: wyłącznie dokładne wersje SDK `x.y.z`, np. `['8.0.415']`; instalacja przez `--version` i kontrola dokładnego numeru; brak albo skróty `8`/`8.0` = błąd przed pobieraniem |
| `docker.sh` | `docker.io` z Ubuntu, **runc 1.2.6** z wydań GitHub (suma SHA-256 przypięta, ta sama co w P-1; tylko amd64) jako domyślny runtime w `/etc/docker/daemon.json` (decyzja `runc-1-2-6-w-pudelku`), drop-in **`/etc/systemd/system/docker.service.d/proxy.conf`** z `HTTPS_PROXY`/`HTTP_PROXY` (i małymi literami) = `TIDE_BOX_PROXY` i `NO_PROXY`/`no_proxy` = `TIDE_BOX_NO_PROXY` (`BOX_NO_PROXY` wstrzyknięte przez builder — skrypt odmawia przy pustym), `agent` w grupie `docker`, restart `dockerd` i sprawdzenie `systemctl show docker -p Environment`, domyślnego runtime'u i `docker info` jako `agent`. `DOCKER_HOST` nie jest ustawiany | `docker.enabled` (nesting ustawia builder) |
| `python-qt.sh` | Python 3, `python3-venv`, `pip`, PyQt6 z QML (`python3-pyqt6`, `python3-pyqt6.qtquick`, moduły `qml6-module-qtquick*`) z archiwum Ubuntu, `QT_QPA_PLATFORM=offscreen`; `xvfb` i czcionki są w obrazie bazowym; sprawdzenie `QGuiApplication` offscreen. PySide6 z PyPI nie jest instalowane (projekt dopisuje przypiętą wersję w `setup`) | — |

**Zmienne stosów** (`write_stack_env` z `lib.sh`) trafiają w te same trzy miejsca co proxy obrazu bazowego:
`/etc/environment`, `/etc/profile.d/tide-<stos>.sh` i `/etc/systemd/user.conf.d/91-tide-<stos>.conf`
(`DefaultEnvironment` usług `systemd --user`, czyli kroków `tide-step`). Wartości są sprawdzane (bez spacji,
cudzysłowów i `$`).

**Domeny tylko budowy.** Szablon może mieć poza `domains` listę `build-domains`: domeny dla skryptów i usług
obrazu wyłącznie w czasie budowy. `templates.build_domains(profile)` (lista budowy dla proxy) zawiera je,
`templates.work_domains(profile)` (polityka kroków w `proxy.compose_policy`) i `known_domains()` (klasyfikacja
pozycji profilu) — nie. Dziś: `docker` → `github.com` (czerwone jako domena pracy — repozytorium zdalne)
i `release-assets.githubusercontent.com` (wydanie runc), `dotnet` → `aka.ms` (kanały `dotnet-install.sh`),
`base` → `motd.ubuntu.com` (serwis powitalny Ubuntu; wyjaśnione odmowy z próby PT-192 z 18:38). Domeny
pracy dopisane w PT-192 z dzienników P-1/P-6: `production.cloudfront.docker.com`, `crl3/crl4/ocsp.digicert.com`
(sprawdzanie podpisów paczek NuGet), lustra `playwright-akamai/verizon.azureedge.net`.

**`STACKS_VERSION`** (`box/builder.py`) to zapisana suma katalogu `box/stacks` (`templates_version()`: wszystkie
`*.yaml` i `*.sh`, także `lib.sh` i `base.sh`). Wchodzi do hasha przepisu jako `templates`, więc każda zmiana
szablonu (np. podbicie `PLAYWRIGHT_VERSION` albo przypięcia Node) robi zbudowane pudełka nieaktualnymi.
`test_box_stacks.py` porównuje sumę katalogu z zapisaną wartością — zmiana szablonu bez podbicia stałej nie
przechodzi testów. Po zmianie: nowa suma do `STACKS_VERSION`, potem `tide box build <slug>`.

**Ograniczenia.** Archiwum Node i `dotnet-install.sh` są sprawdzane sumą z tego samego źródła (integralność, nie
niezależne przypięcie); runc ma sumę przypiętą w repozytorium. SDK .NET wymaga dokładnego numeru w profilu
(np. `dotnet.sdk: ['8.0.415']`); profile ze skrótami `8`/`8.0` trzeba uzupełnić i ponownie zatwierdzić przez
`tide box approve`. Zmiana numeru SDK zmienia hash przepisu, a zmiana szablonu — `STACKS_VERSION`.
`verify` z profilu biegnie po szablonach, `apt`,
`setup` i `docker pull` (krok 8 budowy).

Testy: `daemon/tests/test_box_stacks.py` — tekst skryptów (reguły wyżej, `shellcheck`, jeśli jest), funkcje plików
po `source` w katalogu tymczasowym (drop-in `dockerd` porównany z `netenv.proxy_env`, `daemon.json`,
`NuGet.Config`, zmienne stosu, rozwiązywanie wersji Node i SDK), budowa na atrapie `incus` z prawdziwymi `*.yaml`
i prawdziwym `ProxyService` z #5 (lista domen przed pierwszym `incus exec`, `TIDE_BOX_*` w każdym `exec`).
Pierwsza prawdziwa budowa i pomiary (budowa ≤ 15 min, reset ≤ 60 s, Docker z proxy w pudełku): `bash
scripts/box/stacks-trial.sh` poza piaskownicą — profile próbne python-qt (`plasma-tide`) i dotnet + docker
(`tide-stacks-test`), chwilowe `restricted.snapshots=allow` w projekcie (cofane na końcu), log
`docs/box-prototype/pt192-<czas>.log`; pudełka i profile próbne są usuwane po próbie.
Skrypt zwraca kod 1 po nieudanej budowie, kontroli, resecie, przekroczeniu progu czasu albo błędzie
sprzątania. Gdy usunięcie pudełka się nie powiedzie, zachowuje jego profil próbny do ponowienia sprzątania.
Pomiary mają kropkę dziesiętną niezależnie od ustawień języka hosta.
Jeśli zmiana ustawienia migawek wymaga hasła, próba bez terminala używa `sudo -A` z `SUDO_ASKPASS` albo
wykrytym `ksshaskpass`/`ssh-askpass`. Hasło wpisuje się w oknie systemowym; nie trafia przez stdin skryptu
ani do dziennika próby. Ten sam mechanizm działa przy cofaniu ustawienia, także po wygaśnięciu zgody sudo.
Bez terminala i pomocnika pozostaje `sudo -n` (brak wcześniejszej zgody przerywa próbę przed profilami).
Nieudany odczyt początkowego ustawienia migawek również przerywa próbę, zanim zmieni ona projekt.
Kontrola Docker po budowie sprawdza `docker info` i lokalny `testcontainers/ryuk:0.11.0` jako agent.
Obraz pobiera builder, gdy proxy i polityka budowy działają; po budowie proxy CLI jest już zatrzymane.
Skrypt nie próbuje wtedy pobierać dodatkowego `hello-world`.
`bash scripts/box/stacks-acceptance.sh` wykonuje poza piaskownicą próbę oraz pełne `make test`, również
gdy próba się nie powiedzie. Wynik końcowy zachowuje błąd dowolnej części; pełne wyjście jest w dzienniku
polecenia poza piaskownicą.

Potwierdzone wyniki PT-192 na Incusie 6.0.0: python-qt `clean` w 58,8 s, dotnet + docker `clean` w 76,5 s,
reset z zachowaniem `/home/agent` w 1,5 s; oba dzienniki odmów budowy puste. Pełne `make test` przeszło.
Hashe, wersje, kontrole proxy i źródła pomiarów: [pierwsza budowa szablonów](box-prototype.md#pierwsza-budowa-szablonów-stosów-pt-192).

### Proxy wyjściowe (PT-168)

`tide_daemon/box/proxy.py` to jedyna droga pudełka do sieci i do Ollamy hosta: nftables wpuszczają z `incusbr0`
tylko `<adres mostka>:<box.proxy-port>`, pudełko nie ma DNS ani NAT. Proxy działa w usłudze, we własnym wątku
z pętlą asyncio, i nigdy nie zmienia nftables ani nie woła `sudo` (test przeszukuje kod usługi).

**Start.** Ustawienia w sekcji `box` pliku `$XDG_CONFIG_HOME/tide/cardflow.json` (`box/settings.py`):

```json
{"box": {"enabled": true, "proxy-port": 18080, "bridge": "incusbr0"}}
```

Proxy startuje razem z usługą tylko przy `enabled: true` (domyślnie wyłączone; zła wartość — wyłączone), gdy jest
polecenie `incus` i mostek ma adres (`incus network get incusbr0 ipv4.address`; z projektu ograniczonego
`incus-user` to polecenie bywa niedostępne — na tym komputerze zawsze — wtedy zapasem jest adres interfejsu
`ip -j -4 addr show dev incusbr0`, ten sam, który wpuszczają reguły nftables). Zapas jest słabszy niż odczyt
z Incusa: sprawdza tylko, że interfejs o nazwie z `box.bridge` jest mostkiem w jądrze (`ip -j -d link show`,
`linkinfo.info_kind == "bridge"`), więc `"bridge": "wlan0"` czy `eth0` daje odmowę startu, ale inny mostek
wskazany w ustawieniach wprost (np. `virbr0`) przejdzie — wtedy jego goście dostają 403 na wszystko (brak polityki).
Sprawdzenie, że profil Incusa podłącza pudełka do tego mostka (`incus profile show default`), odłożone: nie wiemy,
czy `incus-user` je widzi. Słucha **wyłącznie** na tym
adresie: `0.0.0.0`, `::`, pętla zwrotna i adres inny niż mostka → odmowa startu. Każda przeszkoda (brak Incusa,
brak mostka, zły adres, zajęty port) to wpis w dzienniku usługi, a usługa działa dalej jak bez pudełka.
`--no-box` wyłącza proxy niezależnie od ustawień. `proxy-port` musi być równy `BOX_PROXY_PORT` z `make box-install`.
PT-292: przed nasłuchem `start_service` czyta zainstalowane reguły `/etc/tide-box/tide-box.nft`
(`box/nftcontract.py`, tylko odczyt; w testach `TIDE_BOX_NFT_FILE`) i odmawia startu, gdy reguła „proxy Tide”
tabeli `inet tide-box` nie wpuszcza dokładnie `box.bridge` → `<adres mostka>:<box.proxy-port>` albo pliku nie ma.
Sekcję `box` zapisuje też `SetCardFlowSettings` (ścisła walidacja, zgodność portu z regułami) — docs/box-cli-v1.md.

**Polityka per pudełko, kluczowana adresem IP.** `set_box_policy(ip, domains, ollama, denied_log_path, *,
full=False, box="")` ustawia (albo zastępuje) politykę pudełka o adresie `ip`; działa od następnego połączenia,
bez restartu proxy (otwarte tunele nie są przerywane). Woła ją krok w pudełku (PT-181) przy starcie kroku
i builder (PT-167) przed pierwszym `incus exec` budowy; `box.proxy.running()` zwraca proxy uruchomione z usługą
(po `stop()` — `None`).
Nieznany adres = brak polityki = każde żądanie 403 (wpis tylko w dzienniku usługi). Klucz IP jest bezpieczny
**wyłącznie dlatego**, że builder ustawia na NIC każdego pudełka `security.mac_filtering`, `ipv4_filtering`
i `ipv6_filtering` (Incus odrzuca pakiety z cudzym adresem źródłowym) — proxy nie umie tego sprawdzić samo;
„podszycie pod IP innego pudełka” jest na czerwonej liście PT-191.

Listę składa `compose_policy(project, cards)`: domeny modeli (`MODEL_DOMAINS`: `api.anthropic.com`,
`statsig.anthropic.com`, `api.openai.com`, `chatgpt.com`, `auth.openai.com` — dokłada usługa, nie profil)
+ `templates.work_domains` profilu projektu (`base`, szablony wg `stacks`, `network.domains` — lista budowy
z buildera bez `build-domains` szablonów, PT-192) + profile aktywnych kart scalone z profilem projektu przez `profile.merge` (czerwone pozycje karty
tylko z `accepted_red`). Usługa sama nigdy nie dokłada `github.com`, `gitlab.*`, adresów wewnętrznych ani
`*.firma.example` (test). `network.full` karty bez `network.full-consent: true` jest pomijane z wpisem w dzienniku
usługi; z jawną zgodą i `accepted_red: [network.full]` daje `full=True`: każda poprawna nazwa i goły adres
**publiczny**. Pełna sieć nie otwiera sieci lokalnej ani adresu hosta na mostku — to osobna czerwona klasa: cel
spoza listy jest zawsze traktowany jako publiczny, więc `CONNECT 10.173.184.1:443`, `10.20.3.10:443`
czy `nas.lan:443` (rozwiązane do sieci lokalnej) dostają 403, chyba że ten cel jest osobno na liście. `host-ollama: true` w profilu projektu albo karty daje `ollama=True`.

**CONNECT bez MITM.** Tylko port 443; dopasowanie dokładne albo `*.domena` (tylko poddomeny:
`*.npmjs.org` nie pasuje do `npmjs.org`, `npmjs.org.attacker.com` ani `evil-npmjs.org`). Nazwy są porównywane
małymi literami i bez jednej kropki końcowej; nazwa z liczbową albo szesnastkową ostatnią etykietą (`10.1`,
`3232235786`) nie jest nazwą. Goły adres IP przechodzi tylko wtedy, gdy jest dosłownie na liście. Nazwę rozwiązuje
proxy i łączy się z adresem, który właśnie sprawdziło: pętla zwrotna, link-local, nieokreślony i multicast — nigdy;
adres prywatny i 100.64/10 — tylko dla celu, który jest na liście pudełka i który profil uznaje za wewnętrzny
(przyjęta czerwona pozycja karty, `BoxPolicy.internal`), więc nazwa publiczna wskazująca nagle do sieci lokalnej
nie przechodzi (403 z rodzajem `ADDRESS`). Po `200 Connection established` proxy tylko
przepisuje bajty w obie strony.

**Zwykłe HTTP.** Do obcej domeny (`GET http://…`) — zawsze 403. Wariantu „zwykłe HTTP do domen z listy”
(`box.proxy-plain-http`) nie ma: apt idzie przez HTTPS (decyzja `apt-przez-https`, `docs/box-decisions.md`).

**Ollama hosta przez proxy.** Żądanie w formie względnej (`GET /api/tags`, czyli `http://<mostek>:<port>/api/tags`
z pudełka) albo bezwzględnej na adres samego proxy idzie na `127.0.0.1:11434` — tylko dla pudełka z `ollama=True`
i tylko ścieżki `/`, `/api/…`, `/v1/…` (reszta 404; `/anthropic/…` i `/chatgpt/…` to trasy K3 niżej). Proxy ustawia `Host:
127.0.0.1:11434` i `Connection: close`, wyrzuca nagłówki „hop-by-hop”, a odpowiedź przekazuje bajt w bajt bez
buforowania (strumień `/api/generate` dociera fragmentami). Inny cel niż `127.0.0.1:11434` nie jest możliwy —
proxy nie jest przekaźnikiem HTTP. `ollama=False` → 403 i wiersz `host-ollama`. Zmian nftables w czasie pracy nie ma.
`models/boxconfig.for_box(data, mostek, port)` robi z `models.json` hosta wersję dla pudełka: punkty Ollamy hosta
(`127.0.0.1`/`localhost`/`[::1]` na 11434) → `http://<mostek>:<port proxy>`; inne punkty, dostawcy i `tokenFile`
nie trafiają do pudełka, a role przypięte do pominiętego punktu znikają (obowiązuje rola domyślna).

**Dziennik odmów.** Odmowa to 403 i wiersz TSV w `denied_log_path` (plik 0600) — w kroku karty
`proxy-denied.log` w katalogu bieżącego kroku, przy budowie ścieżka od buildera (zdarzenie `box.build` w dzienniku
Tablicy):

```
2026-11-04T15:02:11+01:00	tide-proto	10.173.184.23	example.com	443	CONNECT
```

Pola: czas, pudełko (nazwa z `box`, inaczej adres), adres IP pudełka, domena, port, rodzaj (`CONNECT` — cel spoza
listy, `ADDRESS` — cel z listy rozwiązany na adres niedozwolony: pętla, link-local, sieć lokalna dla celu
publicznego; dopisanie domeny tu nie pomoże, `HTTP`, `OLLAMA` z domeną `host-ollama`, `K3` i `K3PATH` z domeną
hosta trasy — niżej). Tylko nazwa hosta z celu żądania — nigdy ścieżka, parametry, nagłówki, atrapa ani treść;
znaki sterujące zamieniane na `?`. Zamianę odmowy w wynik kroku (`denied` z `command: "network: <domena>"`) robi
PT-181, nie proxy.

### Poświadczenia agentów: K3 (PT-175)

Wynik P-3 (`docs/box-prototype.md`): K3 jest możliwe dla Claude Code i dla Codexa, więc w pudełku nie ma
prawdziwych poświadczeń agentów — ani `~/.claude/.credentials.json`, ani tokenów w `~/.codex/auth.json`.
`box/k3.py` to jedno miejsce stanu: `k3_active(program)` zwraca `True` dla `claude` i `codex` (czytają to krok
w pudełku, `tide box login`, dziennik karty #18 i pakiet zgód #9); `login_programs()` — programy do zalogowania
w obrazie (K1) — jest puste, więc `tide box login` (#4) pomija oba programy. Wewnętrzna piaskownica agenta
w pudełku zostaje bez zmian (`sandbox.enabled`, `denyRead: ["~/.claude", "~/.codex"]`).

**Atrapa per krok.** Krok w pudełku (PT-181) na starcie woła `box.proxy.running().grants.issue(step, ip pudełka,
program)` i przekazuje w `in/env` wynik `k3.step_credentials(grant, "http://<mostek>:<box.proxy-port>")`:

- Claude Code: `ANTHROPIC_BASE_URL=http://<mostek>:18080/anthropic` i `CLAUDE_CODE_OAUTH_TOKEN=<atrapa>`
  (`sk-ant-oat01-tide-k3-<256 losowych bitów>`);
- Codex: plik `~/.codex/auth.json` z samymi atrapami (JWT bez podpisu z losowym `tide_k3`; z pliku hosta
  przechodzą tylko identyfikator konta i plan — identyfikatory, bez nich Codex odrzuca konto) i argumenty
  `-c chatgpt_base_url=…/chatgpt/backend-api/ -c model_provider="tide" -c model_providers.tide={ … base_url =
  "…/chatgpt/backend-api/codex", wire_api = "responses", requires_openai_auth = true }`. Bez własnego dostawcy
  Codex woła model przez CONNECT do `chatgpt.com` (P-3) — z atrapą to i tak nic nie da.

Na końcu kroku `grants.revoke_step(step)`; atrapa wygasa też sama po `ttl` (domyślnie 24 h). Atrapa działa tylko
z adresu IP pudełka, któremu ją wydano, tylko na trasie swojego programu i tylko do odwołania; rejestr trzyma
skrót SHA-256 atrapy, nie samą atrapę.

**Trasy wstrzykujące w proxy.** Żądanie w formie względnej (albo bezwzględnej na adres proxy) `/anthropic/<ścieżka>`
→ `https://api.anthropic.com/<ścieżka>`, `/chatgpt/<ścieżka>` → `https://chatgpt.com/<ścieżka>`. Kolejno:

1. pudełko musi mieć politykę (nieznany adres → 403 `K3`);
2. dokładnie jedna atrapa w `Authorization: Bearer` albo `x-api-key`, ważna dla tego adresu IP i tej trasy —
   inaczej 403 `K3` (także atrapa po `revoke_step`, po `ttl`, z innego pudełka, z drugiej trasy);
3. ścieżka z listy z dziennika P-3 — inaczej 403 `K3PATH` (bez ogólnego dostępu do API konta, „confused deputy”):
   Anthropic `/v1/messages`, `/v1/messages/count_tokens`; ChatGPT `/backend-api/codex/…`,
   `/backend-api/wham/accounts/check`, `/backend-api/wham/settings/user`, `/backend-api/ps/plugins/…`,
   `/backend-api/ps/mcp`, `/backend-api/plugins/featured`. Ścieżka z `..`, `.`, `//`, `%` albo znakami spoza
   `[A-Za-z0-9._~/-]` nie przechodzi; zapytanie po `?` tylko ze znaków `[A-Za-z0-9._~!$&'()*+,;=:@/?%-]` —
   inaczej 400. Już wcześniej każde żądanie poza `CONNECT` ma metodę będącą tokenem HTTP, wersję `HTTP/1.0`/`1.1`
   i cel z widocznego ASCII (`0x21–0x7E`), inaczej 400 — goły LF/CR/TAB w linii żądania nie dopisze nagłówków
   (`Host`, `chatgpt-account-id`, `Content-Length`) przed tymi, które wstawia proxy;
4. treść jednego żądania według `Content-Length` albo `chunked` (oba naraz, różne długości, inne kodowanie → 400;
   rozmiar ramki tylko z cyfr szesnastkowych, bez `0x`, znaku, `_` i spacji — inaczej połączenie zamknięte);
   nic po niej (potokowane drugie żądanie) nie idzie dalej;
5. token hosta czytany **przy każdym żądaniu** z `~/.claude/.credentials.json` (`claudeAiOauth.accessToken`;
   `expiresAt` w przeszłości → błąd) albo `~/.codex/auth.json` hosta (`tokens.access_token`, `account_id`).
   To pliki katalogu domowego hosta, nie pudełka (`~/.local/share/tide/box/<slug>/home`) — nigdy nie są montowane;
6. nagłówki z pudełka: `Authorization`, `x-api-key`, `chatgpt-account-id`, `Cookie` i nagłówki połączenia są
   wyrzucane; proxy dopisuje `Authorization: Bearer <token hosta>` (Codex także `chatgpt-account-id`), `Host`
   hosta trasy i `Connection: close`; dla Claude dokłada `oauth-2025-04-20` do `anthropic-beta` (token
   subskrypcyjny bez tej bety nie działa). Linia żądania, kolejność i treść pozostałych nagłówków oraz treść — bez
   zmian (test porównuje bajty);
7. połączenie z hostem trasy jak dla CONNECT (adres sprawdzony po rozwiązaniu, tylko publiczny), TLS klienta
   z weryfikacją certyfikatu w `k3.open_upstream`; moduł `proxy.py` nie ma TLS — po stronie pudełka nie ma
   przechwytywania. Odpowiedź płynie bajt w bajt bez buforowania (SSE).

**Odświeżanie tokenu tylko na hoście.** Proxy nie odświeża tokenu i pudełko też nie (P-3: 0 żądań do punktów
OAuth). Token odświeża Claude Code / Codex hosta przy zwykłej pracy w Konsoli; ponieważ proxy czyta plik przy każdym
żądaniu, odświeżony token działa od następnego żądania bez restartu kroku. Brak pliku, zły JSON albo wygasły token
Claude → 502 z opisem bez treści pliku i wpis w dzienniku usługi; **nie ma cichego powrotu do K1** (proxy nie
kopiuje niczego do pudełka, a krok nie dostaje innego klucza).

**Token nigdy w dzienniku ani w pudełku.** Komunikaty błędów niosą tylko rodzaj problemu; dziennik odmów — tylko
host trasy i rodzaj; `Grant.key` nie występuje w `repr`. Test przeszukuje kod `box/`: plików poświadczeń hosta
dotyka tylko `k3.py`.

**Ryzyko resztkowe K1 przy Dockerze.** W pudełku z Dockerem użytkownik `agent` jest w grupie `docker`, czyli ma
praktycznie prawa roota pudełka: przez kontener może zamontować rootfs pudełka i przeczytać każdy plik
w `/home/agent`, niezależnie od `denyRead` wewnętrznej piaskownicy agenta i od wyniku P-2. To samo może każdy kod
testów uruchomiony w takim pudełku. Przy K1 (osobne logowanie) to byłyby `/home/agent/.claude/.credentials.json`
i `~/.codex/auth.json` z prawdziwymi tokenami. Z K3 tych plików nie ma: kod w pudełku może przeczytać tylko atrapę
bieżącego kroku, która działa wyłącznie przez proxy, z tego pudełka, na ścieżkach z listy i do końca kroku — może
więc w trakcie kroku wywołać model na koncie użytkownika (to samo, co robi agent), ale nie wyniesie tokenu ani nie
użyje atrapy po kroku czy spoza pudełka. Gdyby K3 dla programu zostało wyłączone (`K3_PROGRAMS`), wraca pełne ryzyko
K1: jedyną granicą dla poświadczeń jest pudełko, a lista domen proxy ogranicza tylko, dokąd wyciek może trafić.

**Domeny modeli.** Lista `MODEL_DOMAINS` się nie zmienia w PT-175 (`chatgpt.com`, `auth.openai.com` nadal na niej,
choć z K3 Codex ich nie potrzebuje) — usunięcie to zmiana listy domen, osobna decyzja.

### Krok w pudełku: BoxRunner (PT-169)

`box/runner.py` — `BoxRunner`, drugi `Runner` obok `HostRunner` (`tasks/runner.py`, ta sama umowa `start`, `kill`,
`env`). Uruchamia program agenta kroku w pudełku projektu (`box_name(slug)`) albo w kopii karty (`per_card=True` →
`card_box_name(slug, card)`) jako użytkownik `agent` (uid/gid `box.agent-uid`/`agent-gid`) pod tymi samymi ścieżkami
co na hoście. Wyboru runnera w silniku jeszcze nie ma (#18): silnik dalej dostaje `HostRunner`.

**Polecenie.** Na hoście (widoczne w `ps`) jest tylko:

```
incus exec <pudełko> --project <p> -T --user 1000 --group 1000 --cwd <worktree> --
  /usr/bin/env -i TIDE_STEP=<katalog kroku> XDG_RUNTIME_DIR=/run/user/1000
  /usr/bin/systemd-run --user --unit=tide-step-<id>.service --collect --wait --quiet -p CPUWeight=40 -p IOWeight=40
    --working-directory=<worktree> --setenv=TIDE_STEP=<katalog kroku> -- /bin/sh <katalog kroku>/in/bin/tide-step <argv agenta>
```

Krok jest usługą użytkownika `agent`, a nie scope — decyzja `krok-jako-usluga-user` (P-9): proces w scope ginie
razem z klientem `incus exec`, czyli z restartem usługi Tide; usługa przeżywa, a klient z `--wait` kończy się kodem
jednostki. Nic z `in/env` nie trafia do argv ani do `--env` `incus exec`; klient `incus` dostaje tylko `HOME`,
`PATH`, `LANG`, `USER`, `XDG_CONFIG_HOME`, `XDG_RUNTIME_DIR`, `INCUS_DIR` i `INCUS_CONF` usługi (oraz haki testowe
`TIDE_FAKE*`/`TIDE_INCUS_BIN` atrapy). Wyjście klienta: `out/box-exec.log`.

**Zapis do katalogu kroku.** `<repo>-worktrees` jest w pudełku rw, więc katalog kroku, `in`, `out` i każdy plik mogą
być w chwili startu dowiązaniem podłożonym z pudełka (agent, Codex, root pudełka). Runner nie woła
`Background.prepare()`: `StepDir` otwiera każdy składnik od `<repo>-worktrees` w dół przez `dir_fd` z `O_NOFOLLOW`
(katalogi zakłada `mkdir` bez podążania), pliki kroku (`settings.json`, `gitconfig`, `ignore`, `prompt.txt`) otwiera
z `O_NOFOLLOW|O_NONBLOCK` i przed obcięciem sprawdza `fstat`: zwykły plik, jedno dowiązanie, właściciel — usługa;
`out/box-exec.log` tak samo, dopisywany; `in/env` i `in/local-token` zakłada świeże (`unlink` + `O_EXCL`) przez ten
sam łańcuch; `in/exit-code` usuwa przez `dir_fd`. Dowiązanie, katalog, FIFO albo twarde dowiązanie w drodze →
odmowa kroku, cel nietknięty. Sam `lstat` przed zapisem nie wystarczy (wyścig z procesem w pudełku).

**`in/env` i `tide-step`.** `start` zapisuje `<krok>/in/env` (świeży plik `O_EXCL|O_NOFOLLOW`, 0600, właściciel —
usługa; stary plik albo dowiązanie najpierw usuwa) w wierszach `KLUCZ=wartość` (nowa linia w wartości → odmowa):
całe środowisko programu z `agentcmd.background` (`HOME=/home/agent`, `USER=agent`, `PATH` pudełka, `LANG`,
zmienne gita i martwy D-Bus jak na hoście), sekrety punktu lokalnego (`secret_env`), `HTTPS_PROXY`, `HTTP_PROXY`
(i małymi literami) na `http://<adres mostka>:<box.proxy-port>`, `NO_PROXY`/`no_proxy` = `box_no_proxy(<mostek>)`,
`TIDE_CWD` i zmienne z `step_env(step)` (porty #8, atrapy K3 #18). Adres proxy: jawny `proxy_base`, inaczej proxy
uruchomione w tym procesie, inaczej `incus network get <box.bridge> ipv4.address`; brak — krok odmówiony.
`env(step)` zwraca to samo bez sekretów. Token Claude Code dla punktu lokalnego: kopia 0600 w `in/local-token`
czytana przez `apiKeyHelper` (plik hosta nie jest w pudełku). Node (nvm) i Chromium Playwrighta pochodzą z obrazu
pudełka, nie z hosta: `box_step` zeruje `node_home` i `chrome_bin` kroku (silnik ustawia je z `~` hosta), więc `PATH`
zaczyna się od `BOX_PATH`, a `CHROME_BIN` nie trafia do `in/env` ani `env(step)`.

Opakowanie `daemon/tide_daemon/box/tide-step` (POSIX sh) runner kopiuje przez `StepDir` do `in/bin/tide-step`
każdego kroku, z ochroną przed dowiązaniami. Dzięki temu argv i opakowanie mają tę samą wersję;
starsze `/usr/local/bin/tide-step` w obrazie bazowym nie jest używane przez domyślną ścieżkę runnera.
Nie wymaga to instalowania pliku jako root ani przebudowy obrazu. Opakowanie czyści środowisko,
sprawdza `in/env` (zwykły plik, tryb 0600, właściciel = bieżący
użytkownik — inaczej kod 125; `PATH` = `BOX_PATH` ustawiane jawnie przed pierwszym poleceniem), eksportuje wiersze
bez `eval`, przechodzi do `TIDE_CWD`, zakłada
`CLAUDE_CODE_TMPDIR` w `/tmp` pudełka, uruchamia agenta z `in/prompt.txt` na stdin i wyjściem dopisywanym do
`out/step.log`, zapisuje kod atomowo w `in/exit-code` (jak `EXIT_WRAPPER` hosta; SIGTERM przeczekuje) i kończy się nim.

**`BOX_NO_PROXY`** — jedna stała w `box/runner.py`: `localhost,127.0.0.1,::1,172.16.0.0/12,host.docker.internal`,
a `box_no_proxy(adres)` dokleja adres mostka (nigdy `*`). Używają jej: `in/env` i `env(step)` (tu),
`/etc/environment` obrazu bazowego (#16) i drop-in `dockerd` szablonu `docker.sh` (#29). Sam adres mostka nie
wystarczy: e2e Playwright, Karma i serwery testowe na `localhost` oraz kontenery Testcontainers poszłyby przez proxy
(403). Klienci bez CIDR w `NO_PROXY` i tak trafiają w `localhost`, bo Testcontainers mapują porty na localhost (P-6).

**Montowania** (`ensure_devices`, przy każdym starcie; dodaje tylko brakujące, pod blokadą plikową
`$XDG_STATE_HOME/tide/box/<slug>/devices.lock` — dwa pierwsze kroki naraz dodają je raz):

| Urządzenie | Źródło = ścieżka w pudełku | Tryb |
| --- | --- | --- |
| `tide-git` | `<repo>/.git` | rw |
| `tide-git-hooks`, `tide-git-config`, `tide-git-info` | `.git/hooks`, `.git/config`, `.git/info` (puste katalogi `hooks`/`info` zakładane przed montowaniem) | ro, nad `tide-git` |
| `tide-wtcfg-<nazwa>` | `.git/worktrees/<nazwa>/config.worktree` — **tylko przy `extensions.worktreeConfig=true`** w `.git/config` hosta | ro, nad `tide-git` |
| `tide-worktrees` | `<repo>-worktrees` (worktree i katalogi kroku obok niego) | rw |
| `tide-home` | zapasowe `~/.local/share/tide/box/<slug>/home` (0700) → `/home/agent` — pomijane, gdy builder podpiął już dom projektu lub karty (urządzenie `home`) | rw |

Kolejność montowania: Incus montuje urządzenia `disk` posortowane po `path` (`/` pierwsze, dalej leksykograficznie),
nie w kolejności `device add` — nakładki leżą nad `.git`, bo ich ścieżki mają `<repo>/.git/` jako przedrostek. Nie
„poprawiać” kolejności dodawania; test sprawdza przedrostki ścieżek.

**`config.worktree` (D7).** Git czyta `.git/worktrees/<wt>/config.worktree` tylko przy włączonym
`extensions.worktreeConfig`. Runner czyta flagę z `.git/config` **hosta** (`git config --file`, plik pod nakładką ro —
agent jej nie włączy; błąd odczytu → odmowa kroku). Wyłączona (stan plasma-tide): żadnych nakładek `tide-wtcfg-*` —
nowy worktree ticketu nie wymaga zatrzymania pudełka, kroki projektu startują równolegle. Włączona: nakładka na każdy
istniejący plik, a dla worktree kroku usługa najpierw zakłada pusty `config.worktree` (wskaźnik `<worktree>/.git`
musi prowadzić bezpośrednio do `.git/worktrees/<nazwa>`; dowiązanie albo katalog zamiast pliku → odmowa; plik
zakładany przez `dir_fd` z `O_NOFOLLOW` od `<repo>/.git`, bo `.git/worktrees/<nazwa>` może zostać podmienione
na dowiązanie po sprawdzeniu `realpath`). Nakładka
chroni tylko plik istniejący w chwili montowania i każdy nowy plik wymaga zatrzymania pudełka (przy innych biegnących
krokach — odmowa). Agent może jeszcze założyć `config.worktree` innego worktree bez tego pliku (np. z
`core.hooksPath`); to wykrywa strażnik sum `.git` z #7 (PT-170), a własne wywołania gita usługi chroni
`gitcommand.git_argv` (`core.hooksPath=/dev/null`, `core.fsmonitor=false`).

### Strażnik refów

`box/refguard.py` zapisuje przed uruchomieniem procesu w `BoxRunner.start` pełną migawkę
`git for-each-ref` oraz kopie i sumy SHA-256 chronionych plików: `.git/hooks/`, `.git/config`,
`.git/info/` (także `attributes`), `.git/config.worktree` i `.git/worktrees/*/config.worktree`.
Spis refów uzupełnia odczytem luźnych refów symbolicznych, także z nieistniejącym celem,
które `for-each-ref` pomija. Pliki blokad refów nie należą do tego spisu.
Obejmuje nowe i usunięte pliki, tryby plików oraz dowiązania bez odczytywania ich celów.
Migawki są atomowo zapisywane w `$XDG_STATE_HOME/tide/refguard/<skrót repozytorium>/`
(domyślnie `~/.local/state/tide/refguard/`), poza montowaniami pudełka. Przeżywają restart usługi.

Start, porównanie i przywracanie używają tej samej blokady repozytorium co `gitops` oraz
blokady plikowej `flock`; migawka powstaje dopiero po ich uzyskaniu. Dozwolona jest gałąź
kroku i gałęzie ticketów tej samej karty aktywnych w dowolnym momencie jego trwania.
Nowy krok dopisuje swoją gałąź do starszych aktywnych migawek. Zgoda pozostaje w starszej
migawce po zakończeniu sąsiada, więc równoległe końce nie cofają sobie commitów.
Gałąź pochodzi z przypisania silnika (`Step.branch`), a zgodność `HEAD` z tym przypisaniem
jest sprawdzana przed przygotowaniem i zapisaniem migawki. Podmieniony `HEAD` nie może
rozszerzyć zbioru dozwolonych refów; niezgodność odmawia startu.
Gałęzie innych kart, nowe tagi i pomocnicze gałęzie agenta nie należą do tego zbioru.
Kroki innych kart w tym repozytorium czekają na zwolnienie strażnika; końcowa kontrola
w runnerze odmawia startu, jeżeli dwie karty mimo to równocześnie przejdą sprawdzanie.
Zaufane tworzenie gałęzi, scalenia i sprzątanie worktree przez `gitops` aktualizują w aktywnych migawkach tylko
konkretny ref/config zmieniony przez usługę i tylko wtedy, gdy wcześniejsza wartość zgadza
się z migawką. Runner uwzględnia też założenie pustego `config.worktree` nowego kroku.
Start kolejnego kroku odmawia utrwalenia obcych zmian aktywnego sąsiada jako nowej kopii.

Silnik sprawdza strażnika przy każdym zakończeniu kroku w pudełku, również po zatrzymaniu,
timeout i błędzie programu. Najpierw odtwarza konfigurację i pliki. Zakres naprawy refów to
wyłącznie własna gałąź zapisana przy starcie kroku (`snapshot.branch`), także przy odzyskiwaniu
starszej migawki. Zwykłe commity/reset tej gałęzi są dozwolone; usunięcie lub zastąpienie jej
refem symbolicznym jest raportowane i naprawiane. Zmiany pozostałych dozwolonych gałęzi
sąsiadów są pomijane. Zmiany poza dozwolonym zbiorem są raportowane **bez odtwarzania**:
migawka nie pozwala odróżnić naruszenia od legalnego zapisu innej karty poza `host_operation`.
Raport takiej zmiany nadal kończy krok błędem i pozostawia obcy ref nietknięty.
Naprawa własnego refu używa `git update-ref --no-deref` z oczekiwaną starą wartością;
zmiana po porównaniu powoduje błąd CAS zamiast nadpisania nowszej wartości.
Podmiana własnego refu symbolicznego używa standardowego `<ref>.lock`
i ponownego sprawdzenia OID oraz celu pod blokadą, bez zapisu do celu aliasu.
Kontrola sprawdza też `HEAD` worktree
i wskaźnik `.git`; po naruszeniu `HEAD` ponownie wskazuje gałąź kroku.

Naruszenie zapisuje zdarzenie `refs.guard` z polami `refs`, `files`, `head`, `errors`
i wymusza wynik `error` z powodem `refs.guard`, niezależnie od wyniku agenta.
Brak lub uszkodzenie migawki i nieudana naprawa również kończą się błędem.
Uszkodzony wpis w `worktrees/` lub błąd pojedynczego pliku jest raportowany ze ścieżką;
pozostałe dostępne pliki, refy, `HEAD` i wskaźnik `.git` są nadal sprawdzane i naprawiane.
Przy błędzie naprawy migawka pozostaje aktywna, zachowuje kopię i listę naruszeń.
Zapis `step.finished` nie dezaktywuje migawki z niedokończoną naprawą. Odzyskiwanie
(`recover_network`, także bez proxy) ponawia taką kontrolę w kolejnych tickach z oryginalnej
kopii, aż odczyt i naprawa zakończą się bez błędów; kolejne wyniki zapisuje jako `refs.guard`.
W tym czasie inne karty nadal czekają, a nowy krok tej samej karty nie może przyjąć obcych
zmian jako punktu przywracania. Anulowanie bez kontroli dotyczy wyłącznie nieudanego startu
bez wpisu uruchomionego kroku.
Dopiero pełna kontrola bez błędów utrwala końcowy wynik,
który pozwala ponowić zakończenie bez ponownego cofania refów.

Tryb `--demo` również wykonuje kontrolę po potwierdzeniu nieaktywnej jednostki i zwraca
kod 1 po naruszeniu. Przy nieznanym stanie jednostki lub błędzie naprawy zachowuje migawkę
i wypisuje identyfikator próby. Ponowienie samej kontroli, bez ponownego uruchomienia polecenia:
`python3 -m tide_daemon.box.runner --demo <slug> --worktree <W> --branch <gałąź>
[--card K-N] --finish-guard <demo-id>` (te same projekt, worktree i karta).
Naprawa korzysta z pierwotnej kopii; wykryte wcześniej naruszenie nadal daje kod 1 także
po skutecznej naprawie. Czysta próba zwalnia strażnika i zachowuje kod klienta Incusa.

`HostRunner` nie zakłada migawek i nie uruchamia kontroli. Każde wywołanie gita usługi,
w tym `Git`, `FlowGit`, `StepGit`, odczyty koordynacji i runnera, przechodzi przez
`gitcommand.git_argv`, które dopisuje `-c core.hooksPath=/dev/null -c core.fsmonitor=false`.

Strażnik wykrywa zmiany po kroku; nakładki tylko do odczytu pozostają granicą zapobiegającą
zapisowi haków i konfiguracji podczas pracy agenta. Testy: `daemon/tests/test_refs_guard.py`.

Bez `shift=true` (projekt ograniczony go odrzuca): pudełko dostaje `raw.idmap "both <uid> <agent-uid>"` (P-4,
`raw-idmap-zamiast-shift`). Zmiana `raw.idmap` albo nowa nakładka-plik wymaga zatrzymanego pudełka (pliku nie da się
dołączyć do działającego kontenera), więc runner zatrzymuje je na czas dodania i startuje z powrotem — a gdy według
dziennika pudełka biegną w nim inne kroki, krok jest odmówiony. Z tej listy runner wyklucza własny `step_id`, więc
silnik (#18, PT-181) musi zapisać `box.use` (`state.mark_in_use`) **przed** `runner.start` każdego kroku — inaczej
biegnący krok drugiego worktree, którego wpisu jeszcze nie ma, zginąłby przy zatrzymaniu pudełka. Po takim restarcie runner czeka (najwyżej
`box.ip-wait-seconds`) na menedżer usług użytkownika `agent` (`systemctl --user show-environment` przez `incus exec`),
bo bez niego `systemd-run --user` kończy się kodem 1 (w próbie ręcznej menedżer wstawał ~2 s po starcie); bez
menedżera krok jest odmówiony. Worktree albo katalog kroku spoza `<repo>-worktrees` → odmowa. Zatrzymane pudełko →
odmowa z czytelnym błędem (pudełko uruchamia wcześniej silnik, niżej „Krok w pudełku w silniku”). Błąd `incus` przy montowaniu → `StepError`. Źródło
równe głównemu repozytorium (albo nad nim), katalogowi domowemu hosta (albo nad nim) albo leżące w głównym worktree
poza `.git` → odmowa: główny worktree i `~` hosta nigdy nie trafiają do pudełka. Źródło w repozytorium albo
w `<repo>-worktrees`, którego `realpath` różni się od ścieżki (składnik-dowiązanie, np. `.git/hooks` → `~/.ssh`
podłożone przez rw `.git`) → odmowa, bo Incus dostaje literalne `source`. Kopia karty dostaje urządzenia
we własnej konfiguracji, pudełko projektu zostaje bez zmian.

**Ustawienia agentów w pudełku.** Claude Code: plik `<krok>/in/settings.json` (katalog kroku — ustawienia hosta bez
zmian) z `sandbox.enabled` = `BOX_SANDBOX` (wynik P-2, decyzja `bwrap-profil-w-pudelku`: `true`, z
`failIfUnavailable`), `filesystem.denyRead: ["~/.claude", "~/.codex", "/packs", "<krok>/in/env"]` (+ `in/local-token`),
`allowWrite: [<krok>/out, <repo>/.git]` (nakładki ro pilnują haków i konfiguracji), `network.allowedDomains: []`
(granicą sieci jest proxy Tide), `autoAllowBashIfSandboxed: true`; `--allowedTools` bez list poleceń (`Bash(…)`
wypadają, dochodzi `Bash`; zakazy projektu zostają wskazówkami w prompcie, `--disallowedTools` bez zmian). Wariant
`sandbox=False`: `sandbox.enabled=false` i jeden wpis `box.risk` („ryzyko K1”) przez `journal(kind, fields)`
w dzienniku karty. Codex: `--sandbox workspace-write` bez zmian, serwery MCP hosta nie są pytane
(`codex_mcp=()`).

**Zatrzymanie.** `kill(step)` woła przez `incus exec … --user <agent>` `systemctl --user kill --signal=SIGTERM
tide-step-<id>.service` w pudełku z wpisu (`box`, domyślnie pudełko runnera), czeka `grace` na `is-active`, potem
SIGKILL; klient `incus exec` kończy się razem z jednostką. Nazwy jednostki i pudełka są sprawdzane wzorcem. Stan
jednostki (`unit_state`) ma trzy wartości jak `stepstop.unit_state`: `active` (kod 0), `inactive` (kody 3 i 4
`systemctl is-active`, przekazane przez `incus exec`) i `unknown` (każdy inny kod: błąd klienta `incus exec`,
niedostępny incusd, zatrzymane pudełko, timeout). `kill` zwraca True tylko przy `inactive` — z `unknown` `stepstop`
nie wnosi zatrzymania i nie robi commitu WIP; `alive` przy `unknown` zwraca True.

**Ręcznie** (poza piaskownicą, na pudełku z #1/#4): `python3 -m tide_daemon.box.runner --demo <slug> --branch <gałąź> [--worktree W]
[--card K-N] -- <polecenie>` — krok syntetyczny `<worktree>.demo-<czas>-<losowy-sufiks>.step` z poleceniem zamiast agenta i znacznikiem
`TIDE_DEMO_SECRET` w `in/env` (do sprawdzenia, że `ps -o args` hosta go nie pokazuje); wypisuje `step.log` i kod.
Testy: `daemon/tests/test_box_runner.py` (atrapa `incus`, `tide-step` lokalnie przez `/bin/sh`).

### Dzienniki i przygotowanie zależności (PT-189)

`worktrees/sessions.py` oprócz korzeni hosta czyta domy projektów w
`$XDG_DATA_HOME/tide/box/<slug>/home` (domyślnie `~/.local/share/tide/box/<slug>/home`):
`.claude/projects`, `.codex` i `.codex/steps/*` dla sesji K3. Odkrywanie jest ponawiane przy odświeżeniu.
Kopie per-card mają osobne domy `<slug>/cards/<karta>/home`, skanowane na tych samych zasadach.
Wspólny czytnik `box/logfiles.py` otwiera każdy składnik ścieżki pudełka względem deskryptora katalogu
z `O_NOFOLLOW`, także po podmianie rodzica między wykryciem a odczytem. Ochrona obejmuje Sesje,
Agents, KRunner, koordynację, raporty i indeksy JSONL. Ścieżek pudełek nie rozwiązujemy przez `realpath`.
Bazy SQLite pudełek pomijamy (SQLite sam otwiera WAL po nazwie); tytuły pochodzą z `session_index.jsonl`
albo dziennika. Domy hosta zachowują obsługę dowiązania snapa `current`.
Kopie Claude skaner łączy po identyfikatorze, a Codexa po względnej
ścieżce pliku dziennika, zachowując osobne segmenty. Katalog roboczy sesji pozostaje ścieżką worktree,
więc powiązanie z krokiem karty nie wymaga przepisywania ścieżek. Wyszukiwanie dziennika do przekazania
(`tasks/stepstop.py`) korzysta z tych samych dodatkowych korzeni.

**Przejście ze starego wolumenu.** `Builder.ensure_home`, przed krokiem, przebudową i resetem, sprawdza
montowanie domu. Atomowa zmiana urządzenia czyta konfigurację z `incus list --format json`, zachowując
zapisywalne pola instancji i wielowierszowy `raw.idmap`, bez ograniczeń parsera YAML.
Dla dawnego `<pudełko>-home` zatrzymuje instancję (bez czynnych kroków) i wykonuje
[`incus storage volume export`](https://linuxcontainers.org/incus/docs/main/howto/storage_backup_volume/)
z `--volume-only --compression=none`. Inna instancja korzystająca z tego wolumenu blokuje migrację.
Eksport pozostaje w prywatnym `.home-migration-*` obok domu. Rozpakowanie przyjmuje tylko `backup/volume`,
odrzuca ścieżki wychodzące poza kopię, dowiązania jako rodziców plików i pliki specjalne.
Symlinki są tworzone na końcu, bez podążania za nimi. Właścicielem kopii jest użytkownik usługi;
zachowane są treść, symlinki, czasy plików i zwykłe bity uprawnień, bez UID/GID, suid i atrybutów specjalnych.
Zapis `home-migration.json` pozwala wznowić publikację po awarii; opublikowany dom nie jest nadpisywany ponownym
eksportem. Niepusty dom bez tego zapisu oznacza konflikt i odmowę. Zmiana urządzenia jest atomowa;
błąd nie usuwa starego montowania. Przy błędzie migracji instancja może pozostać zatrzymana.
Stary wolumen, eksport i dom pozostają także po `destroy`. Reset starej migawki ponownie podpina dom hosta,
bez cofania nowych dzienników. `BoxRunner` i `engine._launch` nie uczestniczą w migracji.

`tasks/preparation.py` jest wołane przez `boxstep.configure_background`, po montowaniach i konfiguracji proxy.
`node.install` i `dotnet.restore` otrzymują runner z istniejącego `Placement`; polecenia wykonuje `incus exec`
jako użytkownik `agent`, z katalogiem worktree i środowiskiem pudełka. Wartości środowiska, w tym tożsamość proxy,
przechodzą przez prywatny plik `in/prepare-env` (0600), usuwany po poleceniu; nie trafiają do argv incusa.
NuGet używa `/home/agent/.nuget/packages`, CLI `/home/agent`, Node jest wyszukiwany wewnątrz pudełka,
a Chromium w `/home/agent/.cache/ms-playwright`. Wynik przygotowania trafia do `out/prepare.log` i promptu.
Niepowodzenie nie uruchamia instalacji zastępczej na hoście. Zachowanie `HostRunner` i dotychczasowych
atrap `subprocess.run` pozostaje zgodne z wcześniejszym interfejsem.

K3 Codexa zapisuje sesje w `/home/agent/.codex/steps/<hash katalogu pierwotnego kroku>`;
wznowienie korzysta z tego samego domu. Reset rootfs nie usuwa danych urządzenia domowego.
Prawdziwy test widoczności Sesji i zrzut ekranu pozostają do wykonania po #28.

### Polecenie i czerwona lista: `tide box run` (PT-191)

`python3 daemon/tide.py box run <slug> [--card K-N] [--worktree /ścieżka] -- <polecenie>` uruchamia
polecenie bez agenta przez `BoxRunner.start`. Podział po pierwszym `--` zachowuje każdy argument, także
spacje, puste argumenty i opcje. Kod wyjścia CLI jest kodem polecenia; przygotowanie lub kontrola refów
może odmówić kodem 1, brak portów/zajęte pudełko — 3. `Ctrl+C` próbuje zatrzymać jednostkę i daje 130.
Środowisko jest budowane od zera, bez tokenów agentów hosta, z proxy, pełnym `NO_PROXY`, portami z
`box/ports.py` PT-171 i zabezpieczeniami gita. Wartości `in/env` (0600) nie są argumentami klienta Incusa.
CLI nie loguje agenta, nie wydaje grantu K3 i nie czyta konfiguracji MCP hosta.

Repozytorium musi odpowiadać slugowi projektu w Tablicy. Domyślnie polecenie działa w bieżącym worktree.
Z głównego katalogu powstaje worktree `<repo>-worktrees/demo-…`, gałąź `box-run/demo-…` bez upstreamu
i tymczasowy katalog kroku. Worktree i logi pozostają do obejrzenia, ścieżki trafiają na stderr. Montowania
są te same co w kroku, w tym nakładki ro na konfigurację i haki `.git`; główny worktree nie jest montowany.
`--card` respektuje `box: per-card` i dołącza zaakceptowane, już przygotowane kopie paczek PT-173 jako
`/packs/<nazwa>` ro. CLI nie kopiuje źródeł użytkownika; brak kopii, dowiązanie w ścieżce lub paczki innej
karty oznaczają odmowę. Po potwierdzonym końcu jednostki paczki są odpinane.

CLI używa `base.ensure_proxy`, czyli tej samej instancji `ProxyService` w procesie albo własnego proxy
na statycznym porcie mostka, jak `tide box build`. Nie steruje proxy innego procesu: gdy port zajmuje
działający demon, odmawia zamiast wybierać inny port lub omijać politykę. Wymaga bezczynnego pudełka
i trzyma blokadę jego cyklu życia na czas polecenia. Domeny pochodzą z `compose_policy` projektu i karty,
a `boxaccess` nadaje tożsamość dziennika bez tokenu agenta. Po utracie klienta nieznany stan jednostki
zachowuje rezerwacje; następne `run` ponawia odzyskanie po potwierdzeniu `inactive` i zwolnieniu
odziedziczonej blokady życia z PT-171. Kontrola refów PT-170 obowiązuje także zwykłe polecenie.

`make box-check BOX=<slug>` woła `scripts/box/check.sh` przez `tide box run` i drukuje tabelę
„punkt → odmowa/przeszło/nie dotyczy”. Czerwona lista obejmuje SSH i git-credentials hosta, główny worktree,
HTTP(S) z proxy i bez niego, DNS, Docker i D-Bus hosta, push, brak gh, przestrzeń PID hosta, zapis do `/etc`,
Postgres hosta, podszycie IP oraz osobny dowód z Bash agenta. Każde „przeszło” daje kod 1. Brak programu,
origin, uprawnienia do nadania IP lub rzeczywistego Bash agenta to jawne „nie dotyczy”, a nie zaliczona odmowa.
`CHECK_ARGS="--host-ip <IP-hosta> --spoof-ip <sprawdzony-wolny-IP>"` wskazuje adresy prób; opcjonalny
`--spoof-image <lokalny-obraz-z-ip-i-curl>` pozwala nadać IP przez Docker `--privileged --net=host`
bez pobierania obrazu. Skrypt wysyła `curl --interface` dopiero po skutecznym `ip addr add` i usuwa adres
w trap. HTTP 403 też znaczy „przeszło”: pakiet doszedł do proxy. Na hoście trzeba potwierdzić brak wpisu
z próbnego IP. Docker bez K3 oznacza „ryzyko resztkowe K1” dla poświadczeń Claude; pozostały wariant wymaga
sprawdzenia `cat ~/.claude/.credentials.json` narzędziem Bash rzeczywistego agenta według P-2.

Pełny dowód i zasady pierwszego kroku: [Pierwszy krok w pudełku](box-prototype.md#pierwszy-krok-w-pudełku).
Testy z atrapami nie potwierdzają izolacji prawdziwego Incusa. `scripts/box/boundary-trial.py` zbiera
próbę CLI, `ps -o args`, tabelę i pełne `make test` poza piaskownicą; osobna karta testowa nadal wymaga
zatwierdzonego planu, dziennika, runnera oraz czasu kroku bez `envAnswer`/`outside`.
Opcja `--prepare-missing` tego skryptu odtwarza brakujące środowisko: pozostawia istniejące pudełko i profil,
buduje tylko brakujące pudełko, a przy braku profilu zatwierdza jawny
`docs/agents/pt-191/plasma-tide-proof.box.yaml`. Pakiety narzędzi prób są instalowane w pudełku, bez zmian
pakietów i ustawień hosta. Domyślne wywołanie skryptu nie zatwierdza profilu ani nie buduje pudełka.
Przy starszej konfiguracji projektu Incusa osobna, jawna opcja `--enable-snapshots` ustawia przez sudo
`restricted.snapshots=allow`, zgodnie z instalatorem PT-182 (trwale, po odczycie i z kontrolą wyniku).
`--retry-failed-build` ponawia tylko pudełko w stanie błędu budowy z hashem zgodnym z profilem i bez kroków,
z zachowaniem zabezpieczeń buildera. Nie zastępuje istniejącego profilu ani nie przebudowuje pudełka clean.

### Krok w pudełku w silniku (PT-181)

`cardflow/boxstep.py`, wołane z `FlowEngine._launch`, `_poll`, `_kill` i `_finish`. Dowód na prawdziwym pudełku
(`tide box run`, czerwona lista, pierwszy prawdziwy krok) jest w #28.

**Które kroki idą do pudełka.** Stała `BOX_STEP_KINDS` w `cardflow/engine.py`: `ticket`, `fix` (poprawki po
review), `review` i `close` (domknięcie karty) — kroki, które uruchamiają kod projektu. Planowanie w każdej odmianie
(`planning`, `plan-fix`, `plan-amend`, `plan-revise`, `plan-review`, pełne planowanie w Konsoli) i `conflict` zostają
na hoście w bwrap (`HostRunner`), bo czytają repozytorium i Tablicę. Wznowienie (`parentStep`) nie wybiera na nowo:
dziedziczy runner wznawianego kroku z jego wpisu (krok z hosta zostaje na hoście, nawet gdy pudełko jest już gotowe;
krok z pudełka zostaje w pudełku, a gdy pudełko jest wyłączone albo projekt stracił profil — błąd karty `box`, nigdy
cichy powrót na hosta).
Dziedziczona jest także konkretna instancja `box`: zmiana profilu `per-project` ↔ `per-card` nie przenosi
wznowienia. Brak zapisanej instancji oznacza czekanie, niespójna tożsamość — jawny błąd; silnik nie tworzy kopii
zastępczej dla utraconego środowiska.
Syntetyczna próba zakończona `local-unavailable` również zachowuje pola runnera i instancji, więc ponowienie po
powrocie wykonawcy pozostaje w tym samym pudełku.

**Wybór** (rodzaj z `BOX_STEP_KINDS` albo wznowienie kroku z pudełka), po kolei:

| Stan | Wynik |
|---|---|
| `box.enabled: false` (PT-289) | `HostRunner` (jak przed K-38) przed wszystkimi niżej wymienionymi kontrolami: bez odczytu `boxWait` (także `environment` i `rebuild`), ekspozycji projektu PT-264, profili, rezerwacji repozytorium, admission i Incusa. Wznowienie kroku z pudełka (`runner: box`) nie przechodzi na hosta: `BoxError`, nic nie jest uruchamiane |
| `boxWait: environment` albo zgoda odroczona przy wyłączonym pudełku (PT-290) | krok wykonawczy ani jego wznowienie nie startuje: `step.waiting` „czeka na środowisko”; także bez profilu lub przy hostowym rodzicu; odroczenie staje się `boxWait: environment` (`source: box-enabled`); planowanie pozostaje dostępne |
| projekt `plasma-tide` (ekspozycja v1, PT-264) | krok wykonawczy czeka na manifest projektu i odebrane montowania |
| projekt bez zatwierdzonego profilu | `HostRunner` (jak przed K-38), chyba że karta już czeka na `rebuild` — wtedy nadal czeka |
| budowa albo reset pudełka w toku | krok nie startuje: „czeka na pudełko (budowa)” |
| pudełka nie ma (Incus), bez `boxWait: rebuild` i bez nieudanej budowy w dzienniku pudełka | `HostRunner` |
| pudełka nie ma po błędzie budowy albo przy `boxWait: rebuild` | nadal `boxWait: rebuild`; brak instancji po nieudanej przebudowie nie przenosi kroku na hosta |
| brak filtrowania NIC, brak migawki `clean`, niezgodny hash instancji lub migawki, albo błąd budowy | `boxWait: rebuild` (zdarzenie `box.wait` z `why`), krok nie startuje: „czeka na pudełko (do przebudowy)”; silnik sprawdza odcisk `user.tide.recipe` konfiguracji migawki z `incus snapshot list` także dla istniejącej kopii karty; przebudowę zatwierdza się z planem (PT-172) |
| `box: per-card` | `Builder.copy_for_card` przy pierwszym kroku karty (gdy kopii nie ma), krok w `card_box_name(slug, karta)` |
| pudełko (albo kopia) zatrzymane | `boxWait: start`, `incus start`, `incus exec <pudełko> -- true` do skutku (najwyżej `box.ip-wait-seconds`), menedżer usług `agent`, adres IPv4; potem `boxWait` czyszczone |
| gotowe | `BoxRunner` |

`step.waiting` z pudełka ma `cause: "box"`; silnik ponawia start w każdym `tick`. `boxWait: rebuild`/`start` czyści
silnik, gdy pudełko znów nadaje się do kroku. Krok w pudełku nie robi na hoście `dotnet restore` ani `npm ci`
(paczki instaluje agent w pudełku przez proxy) i nie dostaje `networkDomains`.

**Wpis kroku.** `intent`/`result` akcji `step-start` i `step.started` kroku w pudełku mają pola `runner: "box"`,
`box` (instancja), `boxSlug`, `boxIp` i `boxAccess: 1` (wymagany zapis kontekstu sieci); krok na hoście ich nie ma
(format bez zmian). Po nich silnik — także po
restarcie usługi, gdy nie ma uchwytu procesu — sprawdza krok (`BoxRunner.step_state`: `incus exec … systemctl --user
is-active tide-step-<id>.service`), kończy go z `result.json` i kodem z `in/exit-code` i zatrzymuje (`stepstop`:
`BoxRunner.kill`, potem commit WIP gitem w piaskownicy na hoście jak dziś).
Zakończenie klienta `incus exec` nie kończy kroku: jednostka może nadal pracować. Stan `active` zachowuje krok,
zajętość pudełka i dostęp proxy/K3, a `unknown` nie dowodzi zakończenia. Dopiero `inactive` pozwala odebrać wynik
i kod z `in/exit-code`; kod klienta nie zastępuje kodu kroku.

**Polityka proxy.** Przed `BoxRunner.start`: `set_box_policy(ip pudełka, domeny, ollama, dziennik, full, box)`
z `proxy.compose_policy(profil projektu, profile kart)` — karty to bieżąca i każda karta z trwającym krokiem w tym
pudełku (karta bez własnego profilu nic nie dodaje). Każdy krok ma losowy identyfikator w adresie
`HTTP_PROXY`/`HTTPS_PROXY` (i odpowiednikach małymi literami), przekazywany przez klienta jako
`Proxy-Authorization: Basic`. `register_step` wiąże identyfikator z adresem IP i dziennikiem odmów kroku:
`$XDG_STATE_HOME/tide/box/<slug>/steps/<krok>/proxy-denied.log` — poza katalogiem kroku, który jest rw w pudełku
(proxy dopisuje do niego jako usługa, a podrobiony wpis zmieniałby wynik kroku). Zaraz potem `box.use`
(`state.mark_in_use`) — przed startem, bo BoxRunner zatrzymuje pudełko na nowe montowania tylko, gdy według dziennika
pudełka nic w nim nie biegnie; nieudany start zapisuje `box.release`. Po kroku: `box.release` i polityka ustawiona
na nowo dla pozostałych kroków w pudełku albo `clear_box_policy`, gdy nie ma żadnego. Ruch bez poprawnego
identyfikatora kroku (np. procesy wspólnego Dockera) trafia do osobnego dziennika pudełka
`$XDG_STATE_HOME/tide/box/<slug>/proxy-denied.log` i nie zmienia wyniku żadnego kroku.
Brak uruchomionego proxy = krok nie startuje (`StepError`).

**K3 i restart.** Przed startem programu chmurowego z `k3_active(program)` silnik wydaje atrapę
`GrantRegistry.issue` i przekazuje `step_credentials`: zmienne Claude do `in/env`, a dla Codexa argumenty trasy
oraz plik `auth.json` w osobnym `CODEX_HOME` sesji kroku (wznowienie zachowuje ten katalog). Klucze nie trafiają
do argv. Po końcu albo nieudanym starcie `revoke_step` unieważnia atrapę i identyfikator proxy. Prywatny plik
`steps/<krok>/access.json` (0600, poza montowaniami pudełka) przechowuje tylko atrapy i zatwierdzony kontekst
sieciowy; nigdy poświadczenia hosta. Nowy proces usługi odtwarza z niego polityki aktywnych pudełek i te same
atrapy przed monitorowaniem kroków. Dotyczy to również zamiaru `step-start` bez zapisanego wyniku startu:
jednostka jest sprawdzana we wskazanym runnerze, a nie na hoście.
Odtwarzanie uzgadnia też zwolnienia na podstawie `step.finished` oraz nieudanego wyniku zamiaru `step-start`.
Awaria po zapisie końca lub przed startem jednostki nie pozostawia trwałego `box.use`: kolejne sprawdzenie
ponawia `box.release`, usuwa `access.json`, unieważnia identyfikator proxy i atrapę K3. Powtórne `_finish` też
ponawia zwolnienie bez drugiego wyniku. Nieudane sprzątanie jest ponawiane bez nowego zdarzenia i bez restartu.
Polityka po odtworzeniu pochodzi z zapisanych kontekstów nadal aktywnych kroków; gdy żaden nie pozostał,
jest zdejmowana. Zwolnienie wpisu użycia i pliku kontekstu działa również przy niedostępnym proxy.

**Artefakty testów kroku (PT-174).** BoxRunner dostarcza `in/bin/tide-test` w PATH agenta.
Prompt ticketu i poprawek każe uruchamiać testy przez `tide-test <polecenie> [argumenty…]`,
także polecenia `verify` profilu i rodziny `npm test`, `npx playwright test`, `dotnet test`,
`make test`, `pytest`, `python -m unittest`. Złożone polecenie: `tide-test sh -c '…'`.
Polityka promptu pudełka pozwala opakować dozwolony test bez osobnej zgody na `tide-test`:
listę zgód porównuje się z poleceniem wewnętrznym (dla `sh -c` z jego treścią).
Zakazy mają pierwszeństwo i obejmują pełne wywołanie oraz polecenia wewnątrz owijki i powłoki.
Owijka nie rozszerza zgody na argumenty ani łańcuchy; sam wpis w `verify` nie nadaje zgody.
Nie ma globalnego przechwytywania poleceń; uruchomienie bez owijki nie daje artefaktu.
Owijka dziedziczy środowisko, stdin i katalog roboczy, łączy stdout/stderr w log oraz przekazuje kod wyjścia.
Każde wywołanie tworzy `out/tests/<n>.log` i atomowo dopisuje rekord do listy `out/tests/summary.json`:
`command`, `argv`, `exit_code`, `started_at` (UTC), `duration` (sekundy), `log` oraz
`passed`/`failed`, gdy parser zna format (pytest, unittest, VSTest, Playwright, Jest/Vitest/Mocha).
Nieznany format daje rekord bez liczników, awaria parsera nie zmienia kodu wyjścia.
Równoległe wywołania rezerwują numery i aktualizują listę pod blokadą; bez wywołań katalog testów nie powstaje.
Artefakty leżą w katalogu kroku obok worktree. Po zakończeniu usługa kopiuje tam także
`out/proxy-denied.log`, jeżeli istnieją odmowy, zachowując źródłowy dziennik poza pudełkiem.
Kartka recenzenta wymienia ścieżki i rozmiary artefaktów ostatniego kroku autora oraz instrukcję
„świeży artefakt = dowód; nie proś o ponowny przebieg”; bez testów mówi „krok bez testów”.
Recenzent ocenia aktualność polecenia i czasu względem zmiany. Kroki HostRunner zachowują dotychczasowe prompty.

**Kartka według runnera kroku (PT-291, K-38 F3).** O dodatkach pudełka decyduje wyłącznie zapisany runner
tego kroku (`placement` w `_launch`), nie profil projektu ani runner autora: `handoff.runner_policy(runner, …)`
i `handoff.review_evidence(runner, autor)`. Krok `box` dostaje owijkę `tide-test`, polecenia `verify`, spis
artefaktów autora (tylko review w pudełku po autorze z pudełka) i ograniczenie pudełka: brak wyniku `outside`,
bo silnik i tak go odrzuca (`box-outside`). Krok hostowy — także przy `box.enabled: true` i po autorze
z pudełka, np. review po wyłączeniu pudełek — dostaje tekst master: kontrakt `outside` hosta, `test-changed`
i zasady pełnych testów bez zmian. Zgodność pilnuje `daemon/tests/test_cardflow_host_cards.py`: kartki
ticket/fix/review/close dla trzech klas projektów z F1 porównywane po wąskiej normalizacji (zakończenia linii,
końcowe białe znaki, katalog tymczasowy) z kartkami generowanymi przez moduły master `292fb204` wczytane
z obiektów gita; wzorce i pochodzenie: `daemon/tests/fixtures/host-cards-master/`. Jedyne dopuszczone różnice
to zamknięta lista `hostcards.APPROVED` (dokładne fragmenty, obecnie PT-183: wskazówka „Do sprawdzenia na pulpicie”).

**Odmowa proxy → wynik kroku.** W `_finish` (krok zakończony sam, nie zatrzymany) wynik zestawia się z dziennikiem
odmów kroku (wiersze `CONNECT` i `HTTP`):

- `question`, którego treść wymienia domenę z dziennika → `{"result": "denied", "command": "network: <domena>"}`
  — pytanie o domenę z PT-172 (pole `network`, bez `envAnswer`),
- `error`, którego `out/step.log` albo `out/box-exec.log` (końcówka) wymienia domenę z dziennika → to samo,
- `done` albo `error` z wpisami, których wynik nie dotyczy → bez zmiany wyniku, notka `proxy-denied.md`
  (zapisana przez usługę w `flow/<karta>/steps/<krok>/`, dopisana do `notes`).

Jeden adres IP pudełka = jedna polityka domen, ale osobne dzienniki identyfikowanych kroków. Wynik i jego
dziennik muszą wskazywać tę samą domenę; odmowa połączenia innej karty nie zmienia wyniku tego kroku.

**`box.risk`.** Pierwszy krok karty w pudełku z `docker.enabled` i `k3_active(program) == False` dopisuje do dziennika
karty `box.risk` (`risk: "K1"`, `box`, `step`, `program`, `text`: „poświadczenia agenta czytelne dla kodu w pudełku
(grupa docker); pełna ochrona: K3”) — raz na kartę.

**Ograniczenia.** `OUTSIDE_PER_PROJECT` i `heavyLimit` dotyczą wyłącznie poleceń poza piaskownicą na hoście; kroki w pudełku liczy
tylko limit kroków dostawcy, więc dwa kroki projektu startują równolegle. Wynik `outside` kroku pudełkowego
nie dostaje zgody na wykonanie na hoście: blokada obejmuje nowe wyniki, ponowne sprawdzanie zgód, decyzje
oraz sam start polecenia. Dotychczasowe `outside*` kroków hostowych działają jak wcześniej.

Testy: `daemon/tests/test_cardflow_execution_box.py` i `daemon/tests/test_stepstop_box.py` (baza `boxflow.py`:
atrapa `incus` z prawdziwym `tide-step`, atrapy `systemd-run`/`systemctl --user` pudełka i proxy).

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
- `daemon/tests/test_board_derive_legacy_wait.py` — PT-110: karta dawnego przepływu ze skończonym planem koordynatora i niescaloną gałęzią, której agent kończy turę pytaniem ze znakiem „?” — syntetyczny dziennik w kształcie prawdziwej sesji koordynatora (podagent na bocznym wątku, kolejka, załączniki, `turn_duration`, `away_summary`) przez `AgentScanner` do kolumny: Czeka na mnie z pytaniem (także po `IDLE_AFTER`), po odpowiedzi i raporcie — Do sprawdzenia,
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
- `daemon/tests/test_worktrees_dbus.py` — `List()`, `Remove()`, `Refresh()`, `Prune()`, `Fetch()` (odmowy `bad-request` i `no-remote`), `PullFastForward()` (introspekcja, `bad-request`, `no-upstream` bez zmiany HEAD, pole `flowBranch`) i `ShowWindow()` przez prywatną magistralę; agent symulowany sesją Claude Code w worktree,
- `daemon/tests/test_worktrees_pull.py` (K-36, PT-353) — `Worktrees.PullFastForward` na prawdziwych repozytoriach z lokalnym gołym „zdalnym”: fast-forward gałęzi głównej w głównym katalogu i gałęzi dołączonego worktree, tylko odczyty i jedno `merge --ff-only` (bez `reset`, `rebase`, `stash`, `push`, `prune`, `--force`); każda odmowa (`no-upstream` z odłączonym HEAD, `dirty` z plikiem nieśledzonym, `agent` z katalogiem agenta i miejscem karty, `card-step` z prawdziwego dziennika kart — zamiar, wynik, karta zakończona, inne repozytorium, nieczytelny dziennik, `diverged`, `up-to-date`, `git-operation` ze śladami operacji i filtrem `smudge`, `locked`, `boundary`) z niezmienionym HEAD i `git status`; ścieżki spoza listy (dowiązanie, `..`, nie-napis) bez gita; stan czytany pod blokadą (worktree brudny po odczycie listy i po wzięciu blokady); limit czasu i wyjątek zwalniają blokadę; dwa wątki z przeplotem wymuszonym zdarzeniami: pull czeka na scalanie karty i nie widzi jego stanu pośredniego, scalanie karty czeka na pull i widzi HEAD po nim; M1: blokada silnika wolna w trakcie fetch, drugie pobieranie `busy`; `WorktreeMonitor.pull` wraca od razu (wątek główny nie czeka), drugie żądanie `locked`,
- `daemon/tests/test_worktrees_fetch.py` — `Worktrees.Fetch` na prawdziwych repozytoriach z lokalnym gołym „zdalnym”: `refs/remotes`, `lastFetch` i liczby nowych commitów, nowa gałąź zdalna, wiele zdalnych, brak `--prune` (usunięta zdalnie gałąź zostaje) i nietknięte HEAD/worktree, dokładne argv (bez `--force`, `--tags`, `+refspec`), zdalny wskazujący na nieistniejący katalog i zdalny HTTP 401 z atrapą `GIT_ASKPASS` niewywołaną, maskowanie `user:token@` w komunikacie i logach, otoczenie (`BatchMode`), limit czasu na zawieszonym gicie, ścieżki spoza listy (dowiązanie, `..`) bez uruchomienia gita, pobieranie mimo blokady repozytorium trzymanej przez scalanie karty (bez `lock_for`, M1) i `busy` dla drugiego pobierania tego samego repozytorium,
- `daemon/tests/test_worktree_open.py` — bramka `Open` (katalog z listy, plik tylko dla Ridera, `..`, dowiązania, rodzeństwo o wspólnym prefiksie) i `WorktreeMonitor.open` z atrapą `Popen` (argv `[rider, plik]`, `cwd` = katalog pliku, terminal odmawia),
- `daemon/tests/test_gitread_diffdata.py` — `Git.Diff` źródła `card` na tymczasowych repozytoriach i dzienniku karty: pliki, hunki i sumy zgodne z `git diff` i `git diff --numstat` od przypiętej bazy, zakres po ruchu gałęzi startowej, `targetRef` (merge-base, ref bez wspólnej historii, ref z `-`), kody `unknown-base`, `unknown-card`, `no-branch`, `bad-request`, gałąź główna `main` i `develop` bez `origin`, karta z grupy kart (repozytorium z `card.started`), brak zapisu do dziennika i refów, pułapka konfiguracji z PT-348 nieuruchomiona; duże diffy (PT-355): limit linii i bajtów z pełną listą plików i `truncated`, plik `large` bez hunków, binarne z `oldSize`/`newSize`, `options.path` (także `large` i przycięty) i `options.context`, walidacja `options`, odczyt strumieniowy `Cancellation.run_capped` (nieskończone wyjście ucięte bez czytania całości, dokładnie limit to nie ucięcie); `GitMonitor` — odpowiedź z puli wątków po pracy w tle, pętla wolna,
- `daemon/tests/test_gitread_diffsources.py` (K-36, PT-349) — `Git.Diff` źródła `ticket` (merge-base z gałęzią karty, drugi ticket bez plików pierwszego, ticket scalony z usuniętą gałęzią: zakres z pary `intent`/`result` akcji `merge` równy diffowi sprzed usunięcia, `no-ticket-range` bez zapisu, z nieudanym scaleniem, z SHA spoza repozytorium i z wartością zaczynającą się od `-`, nieznany ticket i karta), `commit` (pierwszy rodzic, commit scalający, commit początkowy względem pustego drzewa, worktree z listy, `options.path`) i `range` (merge-base, zmiany na `from` poza wynikiem, pełne refy i SHA, brak wspólnej historii); walidacja `repo`/refów (`validate.py`): repozytorium spoza listy, przez dowiązanie i `..` oraz ref z `-` odrzucone `bad-request` bez wywołania runnera, dowiązanie do znanego repozytorium rozwiązane do ścieżki kanonicznej, brak zapisu do dziennika i zmian refów oraz indeksu; `GitMonitor` przekazuje źródło znanych repozytoriów,
- `daemon/tests/test_git_dbus.py` — `/Git` `Diff()` przez prywatną magistralę na karcie „Od razu” z atrapą agenta: introspekcja, diff od przypiętej bazy jak `git diff`, `targetRef`, odmowy, bez nowych zdarzeń i zmian refów,
- `daemon/tests/test_krunner_index.py` — indeks historii na syntetycznych dziennikach (`history.py`, `fixtures/history`): tytuły obu narzędzi (także z bazy stanu Codexa zbudowanej w teście), pierwsza wiadomość z początku dużego pliku, odcięcie po 30 dniach, odczyt tylko zmienionych plików,
- `daemon/tests/test_codex_homes.py` — kilka katalogów danych Codexa na świecie w katalogu tymczasowym (`~/.codex`, `~/snap/codex/33`, `34`, `current` → `34`; nigdy prawdziwe katalogi — `tests/__init__.py` ustawia też nieistniejący `$CODEX_HOME` dla usług uruchamianych bez `--codex-dir`): domyślna lista i `$CODEX_HOME`, `--codex-dir` powtarzane, rozwiązywanie `current` (także po odświeżeniu snapa), starsza rewizja pomijana, kopia tego samego pliku w dwóch katalogach liczona raz (Agents, KRunner, worktrees, raport, koordynacja), segmenty wątku jako jedna pozycja w Agents i KRunnerze, a osobno w raporcie i worktrees, przepięcie obserwacji na nową rewizję po odświeżeniu snapa, „Wznów” w raporcie dla sesji ze snapa, `is_snap` tylko pod `~/snap/codex`, przypisanie procesu ze ścieżką z numerem rewizji i Meta+A do karty Konsole, nowe pliki w obu katalogach zauważone przez obserwację katalogów (bez przeglądu co 30 s), nazwy wątków i baza stanu ze snapa w KRunnerze, wznowienie Codexem ze snapa albo spoza niego,
- `daemon/tests/test_codex_desktop.py` — Codex Desktop: odnośnik tylko z UUID, linia `Exec` (kody pól, cudzysłowy, brak programu), szukanie pliku `.desktop` i `$TIDE_CODEX_DESKTOP`, polecenie jako lista argumentów w `systemd-run --scope`, zmienne ekranu z `systemctl --user show-environment`; uruchomienie z atrapą `chatgpt` (`fakebin.py`) — nigdy prawdziwej aplikacji; wątek główny podagenta (kilka poziomów, nieznany rodzic, pętla), zwijanie podagentów i stan najpilniejszy, podagent bez rodzica, `AgentMonitor.activate` i Meta+A (otwarcie wątku głównego, podniesienie okna `ChatGPT`) z atrapą aplikacji,
- `daemon/tests/test_krunner_core.py` — dopasowanie i ranking, `ai `, teksty po polsku, polecenia `Run` (wznowienie Claude/Codex w Konsoli, przejście do okna żywej sesji, wątek Codex Desktop w aplikacji, projekt, worktree, Rider) z zapisem zamiast uruchamiania; leniwe budowanie indeksu,
- `daemon/tests/test_krunner_dbus.py` — `Match`, `Actions`, `Run` przez prywatną magistralę z sygnaturami KRunnera 6; programy to atrapy (`fakebin.py`: `systemd-run`, `konsole`, `rider`, `claude`, `codex` tylko zapisują wywołanie),
- `daemon/tests/test_krunner_semantic.py` — wyszukiwanie po znaczeniu (PT-19) z atrapą modelu („pojęcia” zamiast bge-m3): tekst sesji (metadane, tickety, `strip_secrets`, przycięcie), plik indeksu (0600/0700, bez tekstów, zepsuty albo obcy — od nowa), indeksowanie przyrostowe, okno 30 dni i limit pozycji, przebudowa po zmianie modelu i wymiarów, wyłączenie (plik znika od razu, czekająca partia przerwana), błędy jako stan, `queue.IDLE` i cisza po innym modelu przed każdą partią, ranking, zapytanie (bez poleceń, adresów, ścieżek i sekretów, `HIGH`, pamięć, nowsze unieważnia starsze, serwer nie odpowiada, budżet czasu), łączenie w `KRunnerCore` (słowa pierwsze, bez powtórzeń, dopisek) oraz `models.embed` na atrapie serwera HTTP (Ollama, OpenAI, złe odpowiedzi, chmura, rola za `default`); w `test_krunner_dbus.py` — usługa z atrapą Ollamy (`--search-delay 0`): indeks na dysku, wynik znaczeniowy w `Match`, a po wyłączeniu serwera same słowa w mniej niż 3 s.
- `apps/newtask/tests/` — okno nowego zadania: logika formularza w `qml6`, zrzuty okna z `--data --grab`, zrzuty ekranu (przeciągnięte, ze schowka, atrapa Spectacle, sprzątanie), backend na prywatnej magistrali z prawdziwą usługą, drugie uruchomienie, instalacja z profilem Konsoli; `kwin/tests/test_new_task_shortcut.py` — skrót Meta+Alt+A,
- `apps/board/tests/test_view.py` (PT-43) — okno uruchomienia z karty z tym, co okno nowego zadania: zrzuty ekranu (przeciąganie, Ctrl+V, „Zrób zrzut” z atrapą Spectacle, usuwanie, sprzątanie po Anuluj, katalog zostaje po starcie) w `images` prośby `Start`, model chmura/lokalny z `/Models` (wyłączony z powodem, linia agenta, gałąź i `tool` za programem agenta lokalnego, blokady), `imagesDir` i stan modeli z prawdziwej usługi,
- `apps/board/tests/test_diff_view.py` (K-36, PT-354) — wspólny widok diffa `apps/common/qml/git/DiffView.qml` na atrapie backendu (sonda `diffprobe.py`, offscreen): lista plików z liczbami i sumą, hunki wybranego pliku, `]`/`[`, Alt+↓/↑, `n`/`p`, Esc, `r` i „Odśwież”, „Czytam…”, błąd jednym zdaniem, otwarcie na pliku z parametru, `unknown-base` z wyborem refu i `Git.Diff` z `targetRef`, nazwy z `<b>` i unicode jako zwykły tekst; `startLabel` (`start.js`: baza lokalna, zdalna, nieznana, nazwa z „/” i znakami specjalnymi, brak daty); duże diffy (PT-355): plik `large` zwinięty z „pokaż” i `Git.Diff` z `options.path`, binarny z rozmiarami, pasek przyciętego diffu i samoczynne doczytanie wybranego pliku bez treści, błąd z ponowieniem, spóźniona odpowiedź po odświeżeniu; kolory tylko z palety (`semantic.added`/`removed`), pakiet i slot `gitDiff` backendu Tablicy,
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

**Dopisanie przez użytkownika (PT-248).** W zarządzaniu zatwierdzonym planem przycisk „Dopisz ticket” otwiera
formularz tytułu, kartki z mierzalnymi kryteriami, klasy, wykonawcy, niezależnego recenzenta i blokerów.
Ta operacja obejmuje wyłącznie **jeden nowy ticket w jednym projekcie karty**. Blokery wskazują istniejące
tickety planu; dopisanie nie dodaje blokerów do dotychczasowej pracy. Karta zakończona, aktywna poprawa,
review, domknięcie lub korekta bazy wykluczają nowy zapis. Przy wstrzymanej karcie review czeka na wznowienie.

Publiczne `EditCardPlan` przyjmuje `{action: "append", planFingerprint, requestKey, title, body, class,
executor, reviewer, blockers: ["PT-42"]}` oraz opcjonalny `project` równy projektowi karty.
`derived.flow.plan.fingerprint` jest odciskiem zatwierdzonego planu i wersji bazy, a `canAppend` informuje
o dostępności formularza. Postęp istniejących kroków nie zmienia odcisku; edycja planu go zmienia.
Nieaktualny odcisk daje `stale-plan`, ponowne użycie klucza z inną treścią — `request-conflict`.
Błędy wspólnego sprawdzania formatu wracają jako `invalid-plan` z `errors[]`, bez zmiany planu i rezerwacji ID.

Udany zapis tworzy pojedyncze zdarzenie `plan.proposed` z `sourceKind: append`, `appendRequest` i
`appendRevision`. Zdarzenie atomowo utrwala propozycję z numerem roboczym oraz oczekującą różnicę.
Nie uruchamia `planning`, `plan-amend` ani `plan-revise`, również gdy pierwotny planujący ma wyczerpany limit.
Wybrany recenzent ticketu sprawdza propozycję istniejącą ścieżką `plan-review`; obowiązuje niezależność modelu
albo siły myślenia oraz zachowanie lokalności. Błąd review używa zwykłych decyzji kroku.
Werdykt `REQUEST_CHANGES` pozostawia uwagi użytkownikowi, bez automatycznej poprawki przez planującego.

Po review użytkownik osobno wybiera `ApproveCardPlan` lub `EditCardPlan {action: "discard-revision"}`.
Zatwierdzenie nadaje jeden trwały numer i kieruje ticket do zwykłej realizacji według blokerów.
Istniejące wiersze wracają bez zmian, łącznie z treścią kartek i przydziałami; postęp pochodzi nadal z dziennika.
Odrzucenie zachowuje dotychczasowy plan. Poprawiony ticket można zapisać z nowym kluczem żądania.
Ponowienie identycznego zapisu (także po zatwierdzeniu lub odrzuceniu) zwraca `already: true`, bez nowej
propozycji. Rezerwacje i odtwarzanie zatwierdzenia korzystają z istniejących trwałych zamiarów.

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

**Testy po scaleniu (K-88, PT-318; `cardflow/mergecheck.py`).** Gdy po trwałym `result` scalenia ticketu wierzchołek
gałęzi karty sprzed scalenia (`before`, pierwszy rodzic commita scalenia) różni się od punktu startu ticketu
(`start`), usługa zapisuje `merge.check-requested {ticket, check, start,
before, after, command, source, fallback?}` z `check` = `<ticket>:<n>`. Polecenie to stały napis Tide z bazą = pełny
SHA `start`: `tests.changed` zatwierdzonego kontraktu testów (`{base}` → `start`, `source: contract`), bez kontraktu
cele `Makefile`/`mk/*.mk` gałęzi karty czytane z gita bez uruchamiania `make` (`make test-changed BASE=<start>`,
a przy samym `test` — `make test` z `fallback: full`). Bez polecenia próba ma `unavailable: true` i daje problem
karty `merge-check-unavailable`; zdarzenia „pominięto” nie ma. Kontrakt, który po podstawieniu nie jest pojedynczym
poleceniem (łańcuch, podpowłoka, powłoka, `env`, zmienne jak `PATH=` — kryterium kolejki poza piaskownicą), daje
taką samą próbę niedostępną z `source: contract, reason: contract-not-single`, bez przejścia na `Makefile`. Do wyniku `0` bieżącej próby
(`merge.check-finished {ticket, check, exit, log}`, zapis: przebieg pod zgodą) nie startują nowe tickety, kolejne
scalenia czekają, a `close` nie startuje; sprzątanie worktree scalonego ticketu biegnie. Inny `exit` (także
`"zatrzymane"`, `"przekroczony czas"`) daje `merge-check-failed`, a wynik nieaktualnej próby nic nie zmienia.
Wyjście z bramki w tym tickecie: `ResolveCardStep {choice: "retry-merge-check", ticket, check}` — nowa próba
`<ticket>:<n+1>` na bieżącym wierzchołku z ponownym wyborem polecenia (drugie wyjście — akceptację — dokłada PT-320,
niżej). Stan bramki liczy `fold`; brakującą próbę
po restarcie (po `result`, przed zapisem próby) usługa dopisuje raz. Scalenia sprzed `merge.check-armed` (karty
trwające w chwili wdrożenia) prób nie zakładają. Jedyny wyjątek od `before != start`: gdy `start..before` to wyłącznie
commity stanu planu, które Tide sam zapisał na tej karcie (SHA z wyniku akcji `plan-commit` w dzienniku, każdy z
jednym rodzicem, `start` przodkiem `before`) — Tide commituje plan po starcie każdego ticketu, więc bez tego każde
scalenie karty z planem byłoby nie-FF. Commit ręczny (także samej kartki), commit lub scalenie innego ticketu zakłada
próbę; nazwy plików nie mają znaczenia.

**Przebieg próby pod zgodą (K-88, PT-329).** Próba z poleceniem dostaje pseudokrok `merge-check-<ticket>-<n>`
(`merge.check-step {step, ticket, kind: merge-check, check, command, after, branch, worktree, stepDir, baseVersion,
canAllow}`) — start procesu w modelu tożsamości, więc jej decyzja i uruchomienie są zwykłym śladem procesu outside;
krokiem karty nie jest (nie pracuje, nie ma review ani wznowienia). Tide nie zatwierdza przebiegu z góry, bo
`Makefile`, kontrakt testów i testy na gałęzi karty pisze agent: zgoda to `step.decision` pseudokroku z `check`,
automatyczna (`auto: true`) tylko z tych samych reguł co prośba `outside` kroku — zapamiętana rodzina poleceń projektu,
zgoda ticketu, `outsideAuto` bez polecenia ryzykownego — i z regułami PT-311: odmowa człowieka tego polecenia w tym
tickecie i wstrzymana karta odbierają zgodę automatyczną (`outside.auto-skipped`). Bez pasującej reguły panel karty
pokazuje zwykłe pytanie o zgodę (`permission`: „Zawsze w projekcie”, „Uruchom raz”, „Nie uruchamiaj”, odpowiedź
`ResolveCardStep {choice, step}`); zgoda dotyczy tylko tej próby, a ponowienie z PT-318 ma nowy pseudokrok i wymaga
nowej decyzji. Po zgodzie przebieg idzie kolejką outside (limit miejsc, kolejność zgód, jednostka `tide-outside-…`,
plik kodu wyjścia, rozliczenie zatrzymanej jednostki z PT-315, limit czasu) w worktree gałęzi karty, gdy jego HEAD to
`after` (albo `after` z samymi commitami stanu planu Tide); polecenie biegnie w całości, bez ponowienia samych
padniętych modułów. Wstrzymana karta nie startuje próby i nie trzyma miejsca w kolejce. Koniec zapisuje
`outside.finished` pseudokroku i `merge.check-finished {ticket, check, exit, log}` z tym samym `exit` (liczba,
`"zatrzymane"`, `"przekroczony czas"`; `log` — pełny wynik). Odmowa zgody (także po zgodzie automatycznej, przed
startem — unieważnia ją) daje problem karty `merge-check-denied` z tym samym wyjściem co porażka (ponowienie).
Po restarcie usługi próba nie startuje drugi raz (start i koniec z dziennika), a wynik zapisany między
`outside.finished` a `merge.check-finished` usługa dopisuje raz.

**„Akceptuj mimo porażki” (K-88, PT-320).** Problem karty `merge-check-failed`, `merge-check-denied` albo
`merge-check-unavailable` ma w panelu karty (Tablica i Sesje) dwa wyjścia: „Ponów próbę” (polecane, PT-318) i
„Akceptuj mimo porażki”. Akceptacja to `ResolveCardStep {choice: "accept-merge-check", ticket, check, after}` —
dokładny identyfikator próby z pytania (`after` — pełny SHA wierzchołka gałęzi karty z `merge.check-requested`).
Usługa zapisuje `merge.check-accepted {ticket, check, after, by: "user"}` i otwiera bramkę tylko dla tej próby;
niczego nie uruchamia. Nieaktualny identyfikator (inny `check`, inny `after`, inny ticket), próba udana, czekająca na
przebieg albo już zaakceptowana, otwarta bramka oraz nieznane pola (`step`, `answer`, `auto`) dają błąd bez zapisu.
Decyzję podejmuje wyłącznie jawne wywołanie: akcje opiekuna (`caretaker_problem`/`caretaker_step`) są odrzucane, a
zgoda automatyczna jej nie obejmuje. Kolejna próba ticketu (ponowienie) albo próba po scaleniu innego ticketu
zaczyna bez akceptacji i znowu trzyma bramkę; `fold` liczy akceptację tylko dla bieżącej próby z tym samym `after`,
więc po restarcie usługi zaakceptowana próba nie zamyka bramki ponownie.

Po scaleniu albo odrzuceniu wszystkich ticketów i sprzątnięciu worktree scalonych ticketów krok
`close` zapisuje `summary.md`. Przy `check: manual` wymaga sekcji „Co przetestować ręcznie i jak”;
przy `check: auto` pomija tę listę. **„Do sprawdzenia na pulpicie” (PT-183):** notka kroku (ticket, review,
poprawki) może mieć nagłówek „Do sprawdzenia na pulpicie” z listą punktów (pozycje mogą zajmować kilka wierszy,
pusta sekcja jest dozwolona; kolejny akapit po pustym wierszu nie dokleja się do pozycji, a bloki ``` są pomijane;
kroki odrzuconych ticketów są pomijane). Przy `check: manual` usługa zbiera te punkty ze wszystkich kroków karty i dopisuje
do podsumowania w `card.closed` podsekcję „Do sprawdzenia na pulpicie (z notek kroków)”; każda pozycja ma odwołanie
„(krok …, ticket …)”, a ta sama pozycja z kilku notek (bez względu na wielkość liter i odstępy) pojawia się raz,
ze wszystkimi odwołaniami. Bez takich sekcji podsumowanie jest bez zmian. Mechanizm nie zależy od projektu ani
pudełka. Prompty ticketu i review mówią agentowi, by test wymagający prawdziwego pulpitu oznaczył w tej sekcji,
a nie prosił o niego użytkownika w trakcie. Zdarzenie `card.closed` pokazuje podsumowanie w „Do sprawdzenia”.
`check: auto` nie scala do gałęzi głównej. Panel daje jawne „Zmiany gałęzi” (widok diffa `Git.Diff`, także w trakcie pracy; `CardBranchDiff` zostaje w usłudze dla zgodności), „Do poprawy…” oraz
„Scal do gałęzi głównej”. Odesłanie z uwagami uruchamia poprawę planu na tej samej gałęzi karty;
dla wejścia „Od razu” usługa tworzy bazę jednoticketowego planu dopiero przy tym żądaniu.
Scalenie do głównej działa w piaskownicy gita, sprawdza lokalne kolizje checkoutu i zapisuje
`card.merged`, a następnie przenosi kartę do Gotowych. Wcześniejsze kroki nie przesuwają
gałęzi głównej.

PT-212: jawne `MergeCardToMain` zapisuje pod blokadą repozytorium parę `intent`/`result`
z akcją `main-merge`. Zamiar zawiera stabilne `operationId`, zlecenie (`requestSeq`), `baseVersion`,
`targetRef`/`targetBranch`, `sourceCommit`, `targetCommitBefore`, planowany `resultCommit`
i początkowe drzewo materializacji (`materializationBaseTree`). PT-207 dodaje `resultIndexTree`:
oczekiwany indeks po wniesieniu wyniku, z niezwiązanymi staged użytkownika. `resultTree` pozostaje
drzewem samego commita scalenia; lokalne staged nie trafiają do tego commita.
Wspólne przygotowanie drzewa pochodzi z PT-199. Commit jest zapisywany
dopiero po zamiarze; jego treść i SHA nie zmieniają się przy ponowieniu. Checkpointy
`main-merge.checkpoint` rozróżniają `prepared`, `materialized`, `ref-moved`, `result-saved`
i `projected`. PT-205: nowe sukcesy obu metod (`MergeCard` i zgodnego wstecz `MergeCardToMain`)
zapisują pojedyncze `card.merged` z `operationId`, `targetRef`/`targetBranch`, `sourceCommit`,
`targetCommitBefore`, `resultCommit` i `baseVersion` ze wspólnego zamiaru, a następnie `card.finished`.
Stare `card.main-merged` nadal oznacza zakończenie do zapisanego `main`; dzienniki nie są przepisywane.
Mieszany zapis zakończenia w jednym zleceniu daje jeden wynik, jedną pozycję historii i raportu.
Przepływ zachowuje `derived.flow` również w Gotowych/Archiwum: `merged` to znacznik zakończenia,
`merge` zawiera zapisany wynik (stare pola `main`/`commit` odczytywane jako cel i wynik).
`mainMerged` pozostaje aliasem znacznika dla dawnych klientów. Snapshot, Sesje, historia, faza scalenia
i raport pokazują rzeczywisty pełny cel. Raport wykazuje zakończenie karty w okresie zdarzenia,
nie zalicza jej do otwartych kart. Zgłoszenie kolejnego błędu nie dopisuje wystąpienia do scalonej karty.
`MergeCardToMain` nadal zwraca `{ok, id, main, already}`; historyczny sukces używa zapisanego celu,
nie dzisiejszej głównej. Przyjmowanie innych celów przez `MergeCard` pozostaje zakresem PT-200.

`CardBranchDiff` bez celu używa bieżącego refu zapisanej bazy (bez zastępowania przypiętego SHA).
Przeciążenie z `targetRef` przyjmuje `refs/heads/…` albo znany lokalnie `refs/remotes/…`;
krótkie nazwy, refy symboliczne i nieistniejące są odrzucane. Przy bazie `unknown` wymagany jest jawny ref.
Odpowiedź zawiera `targetRef`, `targetBranch`, `branch`, `diff` oraz dawny klucz `main` jako nazwę porównania.
Sam odczyt nie zapisuje zdarzeń, nie zmienia bazy ani nie stanowi zgody na merge.

Restart lub ponowienie starego API odnajduje operację bieżącego zlecenia i kontynuuje ją
na zapisanych SHA; tick może odtworzyć wyłącznie już zapisany zamiar. Nierozstrzygnięte pytanie o bazę
blokuje zarówno jawne scalenie (także ponowienie), jak i recovery z ticku do decyzji użytkownika.
Zmiana nazwanej głównej,
obcy ref, indeks albo kolizje lokalnej pracy w scalanych ścieżkach powodują `main-merge.waiting`
i „Czeka na mnie”, bez cofania plików i bez drugiego scalenia. Pliki ignorowane są sprawdzane pod
kątem kolizji nazw, bez odczytywania i hashowania ich treści. Niezwiązane edycje i artefakty nie blokują
recovery. Gdy ref wskazuje `resultCommit` (albo jego potomka po dalszych commitach użytkownika), git jest
zakończony: recovery sprawdza SHA i drzewo wyniku, a następnie dokańcza wynik oraz projekcję niezależnie od dalszej
pracy w indeksie, checkoutcie i źródle oraz od późniejszej zmiany `tide.mainBranch` — polityka gałęzi głównej
dotyczy tylko wykonania przed przesunięciem refu. Kolizje z plikami nieśledzonymi i ignorowanymi sprawdzamy
wyłącznie w scalanych ścieżkach i ich rodzicach (pathspec), bez wyliczania całych ignorowanych katalogów.

Oczekiwanie nie zatrzymuje synchronizacji ruchów użytkownika, poprawki planu ani pozostałych kroków karty.
Po usunięciu przyczyny wznowienie używa tego samego ID. „Do poprawy”, zwrot planu, nowe domknięcie lub ręczne zakończenie
po zamiarze zapisuje `main-merge.abandoned`, jeśli wynik nie trafił do refu celu i nie zapisano sukcesu —
także przy obcym refie (np. commit użytkownika na głównej), z materializacją albo bez niej. Porzucenie nie zmienia plików ani indeksu, zwalnia blokadę nowego zlecenia
i nie pozwala dalej wznawiać starego zamiaru. Kolejne domknięcie wymaga nowego jawnego scalenia.
W tym samym zleceniu nowa operacja może przejąć dokładny indeks porzuconej materializacji przy tym samym
celu i niezmienionym refie; kontrola lokalnych kolizji nadal obejmuje stare i nowe scalane ścieżki.
Bez takiej decyzji obcy ref pozostaje jawnym oczekiwaniem; nigdy nie jest zgodą na ponowne wykonanie,
reset ani nadpisanie. Porzucenie przy obcym refie nie dotyka refu, indeksu ani plików, a materializacja,
która została w checkoutcie, nie jest dziedziczona (wymaga niezmienionego refu) — nowe scalenie odmówi jak przy
obcych staged.
Niezwiązane staged i zmiany poza indeksem pozostają obsługiwane (K-43, PT-207). Kopia indeksu jest
przygotowywana i odświeżana w prywatnym katalogu, z worktree i katalogiem gita tylko do odczytu;
zapis obejmuje wyłącznie obiekty i kopię indeksu. Dopiero po zapisaniu zamiaru `read-tree -m -u`
materializuje różnicę między początkowym i wynikowym indeksem. Niezwiązane wpisy (blob i tryb), ich delta
względem nowego HEAD oraz bajty plików pozostają bez zmian. Nie używamy resetu, stasha ani sprzątania untracked.
Przed zapisem zamiaru ponownie sprawdzamy token podglądu, także po przygotowaniu kopii indeksu.
StepGit ustawia `GIT_OPTIONAL_LOCKS=0`: kontrola czystości źródła przez `status` nie odświeża ani nie
konwertuje jego indeksu do split-index, więc nie unieważnia zgody własnym odczytem.
Recovery przyjmuje wyłącznie zapisany indeks początkowy lub wynikowy; nowe obce staged powodują oczekiwanie.
Częściowy zapis plików z niezmienionym indeksem i kolizją pozostaje w jawnym oczekiwaniu, bez nadpisywania.
Starsze zamiary bez `resultIndexTree` nadal używają `resultTree` jako oczekiwanego indeksu.
Brak checkoutu celu korzysta z etapu PT-206: plan i pełny zamiar przed tworzeniem,
własność zasobów w checkpointach i przekazanie do tego samego wykonawcy. Dotyczy także starego API głównej.
