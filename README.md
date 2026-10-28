# Tide

Własny motyw KDE Plasma 6 z widżetami do pracy z agentami AI. Pierwszy wariant to **Mist**: miękki ciemny grafit, teal i lawenda, pastele do statusów.

- Plan wdrożenia: https://claude.ai/artifact/FbiGQYsWQ3gWQiDHLxvKFv
- Mocki: https://claude.ai/artifact/1h17Au71mmdEX5sNBSaAgC
- Decyzje i ich uzasadnienia: [docs/decisions.md](docs/decisions.md)

## Środowisko

KDE neon, Plasma 6.8, Wayland, dwa monitory (2560×1440 główny i 1920×1080). Motyw instaluje się do katalogu domowego. Sudo wymagają tylko ekran logowania (SDDM) i ekran startowy systemu (Plymouth).

## Struktura

| Katalog | Zawartość |
| --- | --- |
| `theme/palette/` | Palety wariantów w JSON, jedyne źródło kolorów, promieni, przezroczystości i rozmiarów pasków dla generatorów: `mist.json` (praca) i `streamer.json` (aktywność Stream, nakładka na Mist z kolorami marki Streamer; wczytuje `theme.palette.load`), sumy plików Mist do testu regresji (`outputs.py`, `make mist-golden`) i porównanie wariantów (`compare.py`, `make palette-compare`) |
| `theme/wallpaper/` | Tapeta: warianty `cat` (ilustracja kota w `assets/cat`, czuwający i śpiący), `blobs` (plamy z palety), `pictures` (aktywność Stream: obrazy grafika na każdy monitor w `assets/streamer`, pulpit i blokada) i `map` (gradient z liniami mapy z palety, `map.py`, zapas), zawsze jedno płótno na oba monitory (`variant.py`, `generate.py`); wtyczka tapety „Tide: kot” z drzemiącym kotem (`plugin.py`, `org.tide.cat/`) i wspólny z ekranem blokady `qml/CatBackdrop.qml` |
| `theme/activities/` | Opisy wyglądu aktywności (`work.json` — dzisiejszy Mist, `stream.json`): schemat kolorów, styl Plasmy, paleta tapety, wariant ekranu blokady, układ paneli; nakłada je `scripts/activity_look.py` |
| `theme/plasma/` | Styl Plasmy „Tide” (desktoptheme): generator ramek, podgląd (`preview.py`) i sprawdzenie prawdziwym KSvg (`ksvg_check.py`) |
| `theme/lookandfeel/` | Motyw globalny „Tide” (`org.tide.desktop`): wskazania na składniki Tide, podglądy i ekran ładowania (`splash/`, zrzuty z prawdziwego ksplashqml w `splash_shot.sh`) |
| `theme/lockscreen/` | Ekran blokady Tide jako pakiet powłoki Plasmy (QML na bazie ekranu Plasmy 6.8), zrzuty z prawdziwego greetera, sprawdzenie kopii powłoki w zagnieżdżonej sesji (`shell_copy_check.sh`) |
| `theme/icons/` | Nakładka ikon „Tide” i polecenie `tide-icons` (audyt, synchronizacja, podgląd) |
| `theme/sddm/` | Ekran logowania SDDM „tide”: QML, składanie z palety (`build.py`), sprawdzenie przed instalacją (`check.py`, atrapa greetera w `harness/`), zrzut prawdziwego greetera (`greeter_shot.sh`) |
| `theme/plymouth/` | Ekran startowy systemu, motyw Plymouth „tide”: składanie z palety (`build.py`), logika skryptu (`logic.script`), atrapa modułu script (`harness.py`), sprawdzenie przed instalacją (`check.py`), klatki bez roota (`preview.py`), podgląd na prawdziwym plymouthd (`preview_real.sh`) |
| `scripts/` | Kopie zapasowe i przywracanie wyglądu (`backup.sh`, `restore.sh`, `paths.sh`), instalatory komponentów (`sddm_system.py` i `plymouth_system.py` to jedyne uruchamiane przez sudo) i synchronizacja kopii powłoki z ekranem blokady (`shell_sync.py`) |
| `theme/konsole/` | Konsola Tide: kolorystyka (przezroczystość z rozmyciem, znak wodny), profil, arkusz stylów paska kart |
| `theme/terminal/` | Prompt basha z chipami (`prompt.bash`), motywy bat, delty, btop, lazygit, fzf i LS_COLORS (`generate.py`), pasek statusu Claude Code (`statusline.py`) |
| `theme/vivaldi/` | Vivaldi Tide: motywy profili i arkusz CSS interfejsu z palety (`generate.py`); instalator `scripts/vivaldi.py`, podgląd w zagnieżdżonym KWinie `scripts/vivaldi_preview.sh` |
| `scripts/panels/` | Skrypty plasmashell górnych pasków, docka i paska bocznego (zrzut, instalacja, kolejność, odinstalowanie) |
| `tools/` | Narzędzia do ikon: `make_tile.py` (kafelek z oryginalnej ikony), `make_tint.py` (eksperyment z przefarbowaniem, odrzucony) |
| `daemon/` | Usługa `tide-daemon`: sesje agentów, worktrees, raport poranny, bezczynność, prognoza Limitów AI i modele lokalne (`/Models`: stan serwerów, ustawienia punktów, ról i programu agenta lokalnego z okna Tide, test połączenia, pobieranie modeli; `~/.config/tide/models.json`) przez D-Bus ([opis i kontrakt](docs/tide-daemon.md)) |
| `widgets/lib/` | Wspólna biblioteka QML widżetów: karta, kafelek docka, teksty kafelków, miernik, pierścień, wykres słupkowy z historią próbek, kropka i licznik stanu, źródło danych z usługi, stan i słowa modeli lokalnych (`models.js`, `ModelsState.qml`) i otwieranie okna Tide na widoku (`TideWindow.qml`) |
| `widgets/work/` | Kafelek „Praca” `org.tide.work` (PT-40, w docku po prawej i w pasku bocznym zamiast Tablicy i Agentów): „Praca · 10 · ● 3 w toku · ● 2 czeka · ● 2 do spr.” z „＋” nowego zadania; popup z tym, co czeka na ciebie („Przejdź”, przy sesji bez karty „+ karta”), w toku, do sprawdzenia, do zrobienia („Uruchom”) i zwiniętymi Skończonymi, „Otwórz Tablicę”, „Otwórz Sesje”, raport poranny — jedna lista z usługi (`/Board Work()`) |
| `widgets/agents/` | Widżet agentów `org.tide.agents`: kafelek „Agenci · 2 pracują · 1 czeka · 2 gotowe”, lista sesji w popupie; od PT-40 poza dockiem i paskiem (zastąpił go kafelek Praca), pakiet zostaje do wycofania |
| `widgets/limits/` | Kafelek „AI” `org.tide.limits` (w docku po prawej): pozostałe limity ChatGPT i Claude z usługi QuotaApp i kropka modelu lokalnego z tide-daemon; popup z kartami usług i sekcją „Modele lokalne” z linkiem do widoku Modele w oknie Tide |
| `widgets/models/` | Kafelek „Modele” `org.tide.models` — od PT-41 poza dockiem (model lokalny jest w kafelku „AI”), pakiet zostaje na wypadek powrotu: czy serwer modeli lokalnych odpowiada, model w pamięci i ile na karcie, kolejka; popup z serwerami, rolami i trybem raportu |
| `widgets/worktrees/` | Kafelek worktrees `org.tide.worktrees`: ile do sprzątnięcia (i ile GB), ile w pracy; kliknięcie otwiera okno |
| `widgets/stream/` | Widżety aktywności Stream (PT-37) ze stanem z rdzenia Streamer przez D-Bus, nie z tide-daemon (decyzja 79): kafelki docka Stream — Stream `org.tide.stream`, Profil gracza `org.tide.stream.profile`, Observer `org.tide.stream.observer`, Kamera `org.tide.stream.camera` (PT-82), „?” `org.tide.stream.help` — i widżety pulpitu lewego monitora — konsola streamu `org.tide.stream.scenes` (Sceny i pełna karta Stream), czat `org.tide.stream.chat` i cały lewy monitor `org.tide.stream.desk` (konsola streamu albo widok Granie według OBS, PT-83); wspólne w `widgets/lib/` (`CoreSource.qml`, `stream.js`, `StreamCard.qml`, `ProfileList.qml`, `StreamConsole.qml`, `ScenesCard.qml`, `ChatView.qml`) |
| `widgets/monitors/` | Kafelek „Monitory · PC → Xbox” `org.tide.stream.monitors` (PT-38, dock Stream): przełącza wejście prawego monitora po potwierdzeniu w popupie; przełączenie robi tide-daemon (`/Monitors`) |
| `widgets/newtask/` | Kafelek „＋ Zadanie · nowe dla agenta” `org.tide.newtask`: otwiera okno nowego zadania |
| `apps/newtask/` | Okno „Tide: nowe zadanie” (QML + PyQt6, plik `.desktop`, profil Konsoli): repozytorium, Claude Code albo Codex, polecenie, zrzuty ekranu, nowy worktree |
| `apps/common/` | Wspólny Python aplikacji, kopiowany przez `widgets/stage.py` obok programu aplikacji i do widoków okna Tide: `taskimages.py` — zrzuty ekranu zadania (okno „Nowe zadanie” i okno uruchomienia na Tablicy) |
| `apps/tide/` | Okno „Tide” (QML + PyQt6, pliki `.desktop`): pasek widoków Tablica, Sesje, Worktrees, Docker, Raporty, Modele; jedna instancja `org.tide.Window`, otwiera się od razu na wskazanym widoku i elemencie |
| `apps/sessions/` | Widok „Sesje” okna Tide (`backend.py`, `qml/`): koordynatorzy, tablica ticketów (Do zrobienia / W toku / Czeka / Review / Gotowe), oś czasu podagentów, wynik podagenta, diff gałęzi, YouTrack |
| `apps/models/` | Widok „Modele” okna Tide (`backend.py`, `qml/`): serwery modeli lokalnych z przełącznikiem „Używaj tego serwera”, dodawanie i edycja serwera (z tokenem), tryb raportu, role i ich modele, modele na serwerze i pobieranie |
| `apps/worktrees/` | Widok „Worktrees” okna Tide (`backend.py`, `qml/`): grupy W pracy / Do sprzątnięcia / Do decyzji, szukanie, filtry repozytoriów, usuwanie zaznaczonych |
| `widgets/system/` | Widżet `org.tide.system`: procesor, pamięć, karta graficzna i dyski z czujników ksystemstats |
| `widgets/sidebar/` | Nagłówek paska bocznego `org.tide.sidebar`: „Tide” i skrót Meta+S, który wysuwa pasek |
| `kwin/` | Skrypty KWin `tide-agent-jump` (Meta+A) i `tide-new-task` (Meta+Alt+A, nowe zadanie) i ich instalator |
| `krunner/` | Plik wtyczki KRunnera „Tide: agenci i projekty” i jej instalator (wyniki daje tide-daemon) |
| `mk/` | Cele `make` poszczególnych komponentów (`install-*`, `uninstall-*`, katalogi testów) |
| `docs/` | Decyzje projektowe, opis usługi, standard planu pracy agentów (`agent-plan-standard.md`) |

Ekran ładowania jest częścią motywu globalnego (`theme/lookandfeel/splash/`).

## Agenci AI

```bash
make install-daemon install-widget-work     # usługa i kafelek Praca (bez kopii zapasowej wyglądu)
make install-agent-jump                     # skrót Meta+A: przejście do agenta, który czeka najdłużej
make install                                # wszystko, najpierw kopia zapasowa
make test                                   # testy wszystkich komponentów
make preview-widgets                        # kafelki na tle docka, popup agentów, kafelek Praca, pasek boczny i popup limitów, kafelek „AI” z modelem lokalnym: build/preview/dock-tiles-*.png, agents-popup.png, work-*.png, sidebar-*.png, limits-popup-*.png, ai-*.png
```

Gdy agent zaczyna czekać — prosi o zgodę albo zadaje pytanie — przychodzi powiadomienie „Agent czeka: <projekt>” z przyciskiem „Przejdź” (raz na każde wejście w czekanie). Zwykły koniec tury to „skończył” (w kafelku „gotowe”): bez powiadomienia i bez Meta+A, także w wątkach Codex Desktop prowadzonych przez zaplanowane zadanie („zaplanowane”). Meta+A przenosi do okna sesji, która czeka najdłużej (sesja z Codex Desktop otwiera w aplikacji swój wątek — u podagenta wątek główny — przez `codex://threads/<id>`); gdy nikt nie czeka, nic się nie dzieje. Jeśli Meta+A jest już zajęty (instalator to wypisze), trzeba go zwolnić w Ustawieniach systemowych → Skróty i przypisać akcji „Tide: przejdź do agenta, który czeka najdłużej”.

**Nowe zadanie dla agenta.** Meta+Alt+A, „＋” na kafelku Praca w docku albo „＋ Nowe zadanie” w jego popupie i w pasku bocznym otwiera okno „Co ma zrobić agent?”: repozytorium z `~/DevWorkspace` (od ostatnio aktywnych; pisanie na liście szuka), Claude Code albo Codex, zadanie (Ctrl+Enter uruchamia), zrzuty ekranu (Ctrl+V ze schowka, przeciąganie plików, „Zrób zrzut” zaznaczonego obszaru przez Spectacle) i „Nowy worktree” (domyślnie włączony) z gałęzią z polecenia, np. `claude/popraw-naglowek-listy`, do zmiany jednym kliknięciem. tide-daemon zakłada worktree od lokalnej gałęzi głównej (`git worktree add -b`, master/main/develop nietknięte, katalog `<repo>/.claude/worktrees/<nazwa>` albo istniejący `<repo>-worktrees`), otwiera nową kartę w ostatnio używanym oknie Konsoli (bez okna — nowe okno) z tytułem „projekt · gałąź” i uruchamia `claude -- "<zadanie>"` albo `codex --image … -- "<zadanie>"`. Po wyjściu z agenta w karcie zostaje powłoka. Błędy (zmiany na master, istniejąca gałąź, brak programu) okno pokazuje w miejscu; gdy Konsola nie uruchomi zadania, worktree i gałąź są wycofywane. Okno pamięta ostatnie repozytorium, narzędzie, model i przełącznik, a nowa sesja ma w widżecie przez 10 min znacznik „nowe”. Obok repozytorium jest wybór modelu: „Chmura” albo „Lokalny” — model z roli `agent` (Tide → Modele), sprawdzony na serwerze przed startem; bez roli, gdy serwer nie odpowiada albo modelu nie ma, opcja jest wyłączona z powodem i odnośnikiem „ustaw w Tide → Modele”. Przy „Lokalny” zamiast przełącznika Claude Code / Codex jest jedna linia, np. „Codex na qwen3-coder:30b · Ten komputer”, z odnośnikiem „zmień w Tide → Modele”: program agenta lokalnego wybiera się raz w widoku Modele (domyślnie Codex, `agentProgram` w `models.json`), a zapamiętany wybór chmury zostaje nietknięty (decyzja 72). Codex dostaje wtedy `--oss --local-provider ollama --model=<model>` (Ollama na tym komputerze) albo własnego dostawcę przez `-c` (Mac, serwer zgodny z OpenAI, token), Claude Code `--model=<model>` i `ANTHROPIC_BASE_URL` serwera (konektory claude.ai są wtedy wyłączone); token z `tokenFile` nie trafia do argv, pliku zadania ani do poleceń, które agent uruchamia (Codex: zmienna wykluczona z poleceń, Claude Code: `apiKeyHelper`). Karta ma tytuł „projekt · gałąź · lokalnie”, a sesja w widżecie agentów dopisek „lokalny · qwen3-coder:30b”. Na tym komputerze (RTX 3070 8 GB) agent lokalny jest wolny i słaby — to przygotowanie pod Maca (decyzja 70). Meta+Shift+A z mocka jest w Plasmie zajęty (aktywności), więc skrót to Meta+Alt+A (decyzja 47); zmiana: Ustawienia systemowe → Skróty → KWin → „Tide: nowe zadanie dla agenta”.

```bash
make install-daemon install-new-task install-new-task-shortcut install-widget-newtask
make preview-new-task          # zrzut okna na repozytoriach z tego komputera (tylko odczyt): build/new-task.png
```

Zrzuty ekranu zadań zostają w `~/.local/share/tide/tasks/` (agent czyta je stamtąd; `make uninstall` ich nie usuwa). Szczegóły: [docs/tide-daemon.md](docs/tide-daemon.md#nowe-zadanie).

**KRunner.** `make install-daemon install-krunner` dodaje wtyczkę „Tide: agenci i projekty”: po wpisaniu w KRunnerze (Alt+Spacja) nazwy projektu, gałęzi albo fragmentu tytułu rozmowy pokazuje sesje Claude Code i Codexa z ostatnich 30 dni (`Claude · plasma-tide · <tytuł>`, `<gałąź> · 3 dni temu`), projekty z `~/DevWorkspace` i worktrees. Enter wznawia sesję w nowym oknie Konsoli w jej katalogu (albo przenosi do okna, jeśli sesja jeszcze działa), otwiera projekt lub worktree w Konsoli; akcja obok otwiera w Riderze. `ai <słowa>` szuka tylko sesji. Dłuższe zapytanie (od trzech słów, np. „ta sesja o dockerze i portach”, „gdzie poprawiałem raport poranny”) znajduje też sesje **po znaczeniu**, bez wspólnych słów — niżej niż trafienia słowne, z dopiskiem „podobne znaczeniowo”. Liczy to model lokalny (rola Wyszukiwanie, domyślnie `bge-m3` na Ollamie tego komputera, po przełączeniu na Maca — na Macu) z samych metadanych: tytułu, początku pierwszej wiadomości, projektu, gałęzi i tytułów ticketów planu, bez treści rozmów; wektory leżą w `~/.local/share/tide/search/sessions.json` (0600). Gdy serwer modeli nie odpowiada albo nie zdąży, KRunner pokazuje same wyniki słowne. Włącza się i wyłącza w Tide → Modele (decyzja 75). Szczegóły: [docs/tide-daemon.md](docs/tide-daemon.md#krunner).

**Tide Sesje: koordynatorzy i tickety.** Sesja, która zleca pracę podagentom (Claude Code przez `Agent`, Codex przez `spawn_agent`), to koordynator. Okno **Tide Sesje** pokazuje koordynatorów z ostatnich 7 dni (z licznikami ticketów), tablicę wybranego — Do zrobienia, W toku, Czeka, Review, Gotowe — z postępem („8 z 13 ticketów gotowych”) i szczegóły ticketu. Karta mówi po polsku, czym jest zadanie, co faktycznie zrobiono i co się dzieje teraz („Review: 9 drobnych uwag, bez błędów blokujących; poprawki wprowadzone, nie przejrzane ponownie”) — opis pisze model przez lokalny Claude Code albo, z `"summary": "local"`, model lokalny (ten sam co w „Na daily”, wsadowo, z pamięcią i odstępami), a bez niego reguły. Szczegóły ticketu: „Co to jest”, „Co zrobiono”, „Teraz / review” i oś czasu: koordynator zlecił implementację → implementacja skończona → review z uwagami słowami i pierwszym znaleziskiem → poprawki. „Pokaż wynik” otwiera raport podagenta i ostatnie kroki jego pracy (podagenci działają w procesie koordynatora, więc nie ma do czego przechodzić), „Przejdź do koordynatora” — okno jego sesji albo wznowienie w Konsoli, „Diff gałęzi” — `git diff` gałęzi ticketu w Konsoli, „Otwórz w YouTracku” — ticket w przeglądarce. Tickety grupują się po ID z opisu podagenta, gałęzi albo (z prefiksem z planu) polecenia; stan wynika z pracy podagentów, planu w repozytorium ([standard planu](docs/agent-plan-standard.md): `docs/agents/plans/RRRR-MM-DD-<slug>.md`) i YouTracka (koordynacja tylko czyta; token z `~/.config/youtrack/token` albo dawnego pliku MCP LogViewera, opcjonalne ustawienia w `~/.config/tide/youtrack.json`). Widżet agentów pokazuje przy koordynatorze „TOM-20 · 8/13” i link „Sesje”, KRunner — słowo „sesje”, a raport poranny — sekcję „Plany i tickety”. Szczegóły: [docs/tide-daemon.md](docs/tide-daemon.md#koordynacja).

```bash
make install-daemon install-tide-window install-widget-work
make preview-tide-window SANITIZE=1   # zrzut każdego widoku na danych z tego komputera bez treści pracy: build/tide-window-<widok>.png
```

**Okno Tide.** Sesje i Worktrees nie są już osobnymi oknami, tylko widokami jednego okna „Tide” z paskiem po lewej: Tablica, Sesje, Worktrees, Docker, Raporty, Modele (Ctrl+1…6 przełącza widoki). Widżety, KRunner, menu i usługa otwierają je od razu na właściwym widoku i elemencie (`tide.py --view sessions --select claude:<sesja>`), a kolejne uruchomienie tylko je pokazuje. Pasek ma plakietki z usługi (ilu agentów czeka, ile worktrees w pracy); gdy źródła nie ma, plakietka znika. Dawne wejścia działają dalej: pozycje menu „Tide Sesje” i „Tide Worktrees”, przypięte skróty (grupują się pod jednym oknem Tide), nazwy D-Bus `org.tide.SessionsWindow` i `org.tide.WorktreesWindow`, polecenia `install-sessions-window`, `install-worktrees-window` i `tide-worktrees.py`. Domyślny widok to Tablica. Uzasadnienie: decyzje 59 i 61–63.

**Tablica pracy.** Osobisty backlog ze wszystkich projektów: karta to „rzecz do zrobienia” (funkcja, poprawka, pomysł), z projektem, tagami (np. „Firma”, „własne” — dziedziczone z projektu) i opisem. Dane są w lokalnym repozytorium git `~/DevWorkspace/tide-board` (inne miejsce: `--board-dir` albo `$TIDE_BOARD_DIR`), które tide-daemon zakłada przy pierwszej karcie i commituje po każdej zmianie — także po ręcznej edycji plików i planach pisanych przez agentów w `docs/<projekt>/`. Bez remote; tablica nie pojawia się w Worktrees, KRunnerze ani w oknie „Nowe zadanie”. Domyślny tryb pracy nowego projektu to pełny flow. Obiekt `/Board` (`org.tide.Daemon.Board`), podgląd: `/usr/bin/python3 daemon/tide-daemon.py --print-board`. Widok **Tablica** w oknie Tide: kolumny Backlog, Do zrobienia, W toku, Czeka na mnie, Do sprawdzenia i Gotowe; karty przeciąga się między Backlogiem, Do zrobienia i Gotowymi (Ctrl+←/→ z klawiatury), filtry tagów i projektu, szukanie (Ctrl+F), układ „Wiersz na projekt”, nowa karta Ctrl+N albo „＋ Karta”, kliknięcie otwiera edycję (projekt, tagi, opis, notatka, archiwizacja), „Projekty i tagi” na dole paska. W toku, Czeka i Do sprawdzenia wypełni stan koordynatora (PT-5), a okno „Co ma zrobić agent?” po przeciągnięciu do Do zrobienia — PT-4. Okno uruchomienia z karty ma to, co okno „Nowe zadanie”: zrzuty ekranu (Ctrl+V, przeciąganie, „Zrób zrzut”) i model „Chmura” / „Lokalny” (PT-43, decyzja 73). **YouTrack w obie strony** (PT-7): projekt z kluczem YouTracka (np. LOG, LEGAL — w „Projekty i tagi”) synchronizuje stan zgłoszeń z kolumną karty i stanem ticketów planów `tracker: youtrack` (Do zrobienia → Open, W toku → In Progress, Czeka → To be discussed, Do sprawdzenia → Fixed, Gotowe → Verified, powrót → Reopened), tylko przy przejściach widzianych przez Tide, bez nadpisywania zmian ręcznych w YouTracku (konflikt z wyborem „Weź z YouTracka” / „Nadpisz z Tide”) i z kolejką, gdy YouTrack w sieci lokalnej nie odpowiada; „Importuj z YouTracka” zakłada karty w Backlogu z twoich zgłoszeń, bez duplikatów. Kafelek „Praca” w docku (po prawej, przed Limitami AI) i w pasku bocznym (PT-40, zastąpił kafelki Tablica i Agenci): karty tablicy i sesje agentów bez karty z jednej listy usługi — liczby zawsze te same co na Tablicy; sesję bez karty „+ karta” zamienia w kartę połączoną z sesją (decyzja 76). Dawny kafelek Tablica (`org.tide.board`, z polem „Dopisz do backlogu i Enter”) zostaje zainstalowany do wycofania. Szczegóły: [docs/tide-daemon.md](docs/tide-daemon.md#tablica), uzasadnienie: decyzja 57.

**Docker.** Własny podgląd kontenerów zamiast Docker Desktop: obiekt `/Docker` (`org.tide.Daemon.Docker`) daje kontenery pogrupowane według projektów compose z przypisanym repozytorium, porty z linkami, kontenery, które padły (np. kod 137 albo nieudany start), start, stop i restart pojedynczo lub całym projektem, logi i powłokę w karcie Konsoli, CPU i RAM na żądanie oraz porządki (cache budowania, nieużywane obrazy, wolumeny, zatrzymane kontenery) — wyłącznie zaznaczonych kategorii. Podgląd tylko do odczytu: `/usr/bin/python3 daemon/tide-daemon.py --print-docker`; wyłączenie: `--no-docker`. Widok **Docker** w oknie Tide: projekty compose z repozytorium, kontenery z portami (HTTP jako link), CPU i RAM, logi w szufladzie (stderr wyróżnione, „Otwórz w Konsoli”), powłoka w Konsoli, start, stop, restart i usuwanie (z potwierdzeniem) pojedynczo albo całym projektem oraz porządki — potwierdzenie czeka na świeży odczyt miejsca (`DiskNow`) i wymienia dokładnie, co zniknie. Kafelek „Docker” w docku z lewej, zaraz po System („Docker · 6 projektów · ● 9 działa · ● 1 padł”), popup z projektami, portami i kontenerami, które padły; w pasku bocznym go nie ma (brak miejsca obok tablicy). Szczegóły: [docs/tide-daemon.md](docs/tide-daemon.md#docker), uzasadnienie: decyzja 58.

**Raporty w oknie Tide.** Widok **Raporty**: z lewej doby z 30 dni (brzoskwiniowa kropka przy nowym), w środku projekty z ruchem (najpierw te, w których coś czeka na ciebie) z tagami z tablicy, z prawej „Na daily” wybranego projektu z „Kopiuj jako tekst”; „Wygeneruj teraz”, „Otwórz w przeglądarce ↗”. Obejrzenie nowego raportu w oknie gasi kropkę także w widżetach. Raporty sprzed tej zmiany są na liście, ale „Na daily” pokazuje tylko przeglądarka. Uzasadnienie: decyzja 63.

**Modele lokalne w oknie Tide.** Widok **Modele** ustawia modele lokalne bez edycji plików (zapisuje je usługa, `/Models`). *Serwery modeli*: karta na serwer — „Ten komputer” albo nazwa z ustawień (np. „Mac-Studio”), stan (działa / nie odpowiada / sprawdzam…), wersja Ollamy, liczba modeli, model w pamięci i ile z niego mieści się na karcie graficznej, kolejka modeli („Pracuje: raport (qwen3:8b) · 2 w kolejce: opisy kart”, a po zmianie adresu także to, co trwa jeszcze na starym — `queue` w `Status()`); modele chmurowe Ollamy (`…:cloud`, `…:120b-cloud`) są oznaczone „w chmurze Ollamy”, nie ma ich do wyboru w rolach, a „Pobierz model” ostrzega przy takiej nazwie — Tide ich nie używa, bo fakty wyszłyby z komputera; znacznik „Używany” i przycisk **„Używaj tego serwera”** — to przełącznik „ten komputer / Mac”: role bez przypięcia idą za nim. „Sprawdź połączenie”, „Edytuj” (adres, Ollama albo serwer zgodny z OpenAI, token z podglądem i „Usuń token”; okno nigdy nie zna zapisanego tokenu — puste pole go nie zmienia) i „Usuń” z potwierdzeniem; „Dodaj serwer” z „Sprawdź” przed zapisem — tu dodasz Maca. *Do czego używamy modeli*: raport poranny i opisy kart — Claude, Lokalnie albo Bez modelu (działa od razu), a przy rolach Raport, Opisy kart, Agent (pod opcję „Lokalny” w oknie Nowe zadanie; przy Agencie także program: Codex — Ollama obsługuje go wprost — albo Claude Code) i Wyszukiwanie (szukanie sesji po znaczeniu w KRunnerze: Włączone / Wyłączone i jedna linia stanu indeksu, np. „Gotowe: 412 sesji w indeksie · 28.10, 10:02”) model z listy zainstalowanych na serwerze roli, ostrzeżenie z „Pobierz teraz”, gdy go tam nie ma, i „Zaawansowane”: przypięcie do serwera albo „za domyślnym” i kontekst. *Modele na serwerze*: lista z rozmiarem i tym, co jest w pamięci, „Pobierz model” z podpowiedziami z naszych pomiarów i postępem. Błędy i odmowy usługi są przy kontrolce, która je wywołała. Plakietka „● N” w pasku, gdy coś, czego Tide używa, nie działa (np. agent na Macu, który nie odpowiada). Kafelek „Modele” otwiera ten widok. Podgląd: `make preview-models-view` (`build/models-view-<stan>.png`). Uzasadnienie: decyzja 69.

**Raport poranny.** `make install-daemon` (i `install-krunner` dla słowa „raport”): raz na dobę — przy pierwszym logowaniu albo o 07:00, jeśli już jesteś zalogowany — usługa składa w `~/.local/share/tide/reports/RRRR-MM-DD/` `index.html` i stronę HTML na każdy projekt-repozytorium z ruchem albo z pracą w toku (stan prac na dzień: także po cichej nocy widać, co trwa i co czeka). Indeks to przegląd: karta projektu z 1–2 zdaniami „co się działo”, licznikami (zrobione, w toku, czeka, porządki) i tym, co czeka na ciebie — projekty z czymś do zrobienia dla ciebie są na górze; sesje spoza repozytoriów są w zwiniętej sekcji „Poza projektami”. Na górze strony projektu jest jego **„Na daily”**: *Zrobione* (co konkretnie powstało albo się zmieniło i po co — tak, żeby dało się to powiedzieć komuś, kto nie zna kodu), *W toku* (jak daleko i następny krok), *Czeka na mnie* (pytania, decyzje, testy, które nie przechodzą), *Dalej* (tickety `todo` z planu), akapit „Na czym polega” i zwinięte *Porządki* (tracker, plany i instrukcje agentów, konfiguracja — osobno od prawdziwej pracy), z przyciskiem **„Kopiuj jako tekst”** do przeczytania na daily albo wklejenia na czat. Surowe dane — commity po gałęziach, sesje Claude i Codexa z ostatnią wiadomością, przebiegi zaplanowanych zadań, plany i tickety, worktrees, zmiany w głównym katalogu, Limity AI — są niżej, zwinięte w „Szczegóły”. Przychodzi jedno powiadomienie „Raport poranny gotowy — N projektów” z „Otwórz”, a raport zostaje pod ręką w widżetach: w nagłówku paska bocznego „Raport · dziś 07:00” (`make install-widget-sidebar`) i w dolnym wierszu popupu kafelka Praca w docku (`make install-widget-work`), z brzoskwiniową kropką, dopóki go nie otworzysz — z widżetu, powiadomienia albo KRunnera. Raport na żądanie: `qdbus6 org.tide.Daemon /Reports org.tide.Daemon.Reports.Generate`. Raporty trzymamy 30 dni; ustawienia w `~/.config/tide/report.json` (`{"at": "07:00", "summary": "claude", "summaryModel": "sonnet"}`). Szczegóły: [docs/tide-daemon.md](docs/tide-daemon.md#raport-poranny).

*Opis „Na daily” i prywatność.* Domyślnie (`"summary": "claude"`) tekst pisze model przez twój lokalny Claude Code: jedno wywołanie `claude -p --model sonnet` na raport (ok. 30 s), bez narzędzi, bez CLAUDE.md, hooków, wtyczek i MCP, bez zapisu sesji, z limitem 180 s. Model dostaje tylko zwięzłe fakty (ID, tytuły i początki opisów ticketów, cele planów, stany, wyniki review, tematy commitów, obszary zmienionych plików, tytuły sesji i pierwsze polecenia, pierwsze zdania ostatnich wiadomości agentów, pytania) — najwyżej ok. 16 tys. znaków na projekt, po usunięciu wszystkiego, co wygląda na sekret (klucze, tokeny, hasła, linie `.env`), z `~` zamiast katalogu domowego. Te dane idą do Anthropic tak samo jak przy zwykłej pracy z Claude Code i liczą się do twoich limitów: zwykle kilka–kilkanaście tysięcy tokenów wejścia raz dziennie (ponowne `Generate()` z tymi samymi faktami bierze odpowiedź z `summary.json` w katalogu raportu). Bez programu `claude`, po błędzie, przekroczeniu czasu albo przy odpowiedzi niezgodnej ze schematem — i zawsze przy `"summary": "rules"` — raport składa opis z reguł, bez wysyłania czegokolwiek. Z `"summary": "local"` „Na daily” i opisy kart pisze model lokalny z `~/.config/tide/models.json` (role `summary` i `describe`, domyślnie `qwen3:8b` w Ollamie na tym komputerze; `summaryModel` jest wtedy pomijany): te same fakty, instrukcje i walidacja, odpowiedź według schematu JSON, jedno wywołanie na projekt — i nic nie wychodzi z komputera, chyba że rola wskazuje serwer w sieci (np. Mac Studio), wtedy fakty idą tylko do niego. Tryb lokalny nigdy nie przechodzi do Claude: gdy serwer nie działa albo model odpowie źle, raport i karty mają opis z reguł z informacją „Model lokalny niedostępny — opis bez modelu (serwer nie odpowiada, 127.0.0.1:11434)”.

Kafelek „Tide · Praca” stawiają w docku i w pasku bocznym `make install-dock` i `make install-sidebar` (ponowna instalacja zdejmuje z nich Tablicę i Agentów; bez zainstalowanej Pracy stawiają je z powrotem — wycofanie: `make uninstall-widget-work install-dock install-sidebar`); każdy widżet da się też dodać z listy widżetów. Po aktualizacji już dodanego widżetu Plasma wczyta nowy kod dopiero po `systemctl --user restart plasma-plasmashell`. Kolory biblioteki QML są generowane z palety przy składaniu pakietu (`widgets/stage.py` → `Tokens.qml`).

## Limity AI i model lokalny (kafelek „AI”)

Widżet „Tide · AI” (`org.tide.limits`, do PT-41 „Limity AI”) pokazuje w kafelku „AI” pozostały procent ChatGPT i Claude (niższy z limitów sesji i tygodniowego) i kropkę modelu lokalnego, a w popupie oba limity z resetami i sekcję „Modele lokalne”.

```
┌ AI · Claude do jutra 14:30 ───────┐
│ GPT 100%  Claude 30%  ● lokalny   │
└───────────────────────────────────┘
```

**Model lokalny.** Kropka i krótkie słowo w linii procentów, ze stanu domyślnego serwera modeli z tide-daemon (`/Models`, te same słowa i kolejność stanów co dawny kafelek Modele, decyzja 68): „● lokalny” — działa (mięta), pracuje albo pobiera (teal); kolor ostrzegawczy tylko wtedy, gdy serwer nie odpowiada („● lokalny nie działa”, róż), model nie mieści się na karcie („● lokalny na CPU”, masło) albo brakuje modelu roli raportu czy opisów kart („● lokalny bez modelu”, masło). Bez tide-daemon, przed pierwszym odczytem i bez serwera w ustawieniach kropki nie ma; gdy jest, kafelek ma stałą szerokość (miejsce na najdłuższe słowo). Model chmurowy Ollamy w pamięci nie zmienia stanu — Tide go nie używa. Popup: sekcja „Modele lokalne” — serwer z kropką stanu, powód niedostępności, model w pamięci i ile z niego jest na karcie (z terminem zwolnienia), kolejka i pobieranie, problemy ról i pliku ustawień — oraz „Otwórz Modele ↗” (widok Modele w oknie Tide, tam są wszystkie serwery, role i tryb raportu). W pasku bocznym pod limitami wiersz „Lokalny ● qwen3:8b · na karcie 100%” z „Modele ↗”. Podgląd: `/usr/bin/python3 widgets/preview_ai.py` (`build/preview/ai-*.png`).

Dane limitów kafelek bierze wprost z lokalnego API usługi [QuotaApp](https://github.com/julia-lakomy/QuotaApp) (`limity-ai-widget.service`) — usługa musi być zainstalowana i działać.

**Prognoza i powiadomienia** pochodzą z tide-daemon: co 2 min czyta on `/status` QuotaApp (bez wczytywania stron), zapisuje historię odczytów (`~/.local/state/tide/limits-history.jsonl`, 14 dni) i z tempa w bieżącym oknie liczy, kiedy limit się skończy. Popup pod resetem każdego limitu pisze „wyczerpie się ok. 17:40” (brzoskwinia) albo „starczy do resetu”, pasek boczny dopisuje „· koniec ok. 17:40” / „· starczy do resetu”, a kafelek w docku dostaje krótkie „· Claude do 17:40” tylko wtedy, gdy limit skończy się przed resetem. Przy za małej liczbie odczytów prognozy nie ma. Powiadomienie „Claude: zużyto 82% limitu sesji” przychodzi raz na okno przy 80% i 95%, a „… skończy się przed resetem” — raz, gdy prognoza mówi o więcej niż 30 min przed resetem; restart usługi ich nie powtarza. Progi są w palecie (`limits` w `theme/palette/mist.json`). Bez tide-daemon widżet działa jak dotąd, tylko bez prognoz. Szczegóły: [docs/tide-daemon.md](docs/tide-daemon.md#limity-ai).

```bash
make install-daemon install-widget-limits   # prognoza: usługa i widżet (nowy kod widżetu po restarcie plasmashell)
qdbus6 org.tide.Daemon /Limits org.tide.Daemon.Limits.Forecast
```

```bash
make install-widget-limits     # instalacja albo aktualizacja widżetu
make swap-limits-widget        # podmiana starego widżetu QuotaApp w dolnym panelu (kopia zapasowa przed)
make uninstall-widget-limits   # usuwa tylko org.tide.limits
```

**Token.** Port i token API są w `~/.config/limity-ai-widget/config.json` (tworzy go instalator QuotaApp, tryb 0600). Przy instalacji `widgets/stage.py` przepisuje je do `contents/code/Config.js` w pakiecie, z trybem 0600; `widgets/install.sh` usuwa ten plik ze złożonej kopii w `build/` zaraz po instalacji. Token nigdy nie trafia do repozytorium. Po zmianie tokenu wystarczy ponowne `make install-widget-limits`.

**Podmiana w panelu.** `make swap-limits-widget` robi kopię zapasową i przez `evaluateScript` Plasmy usuwa `org.limityai.widget` z dolnego panelu, a w to samo miejsce wstawia `org.tide.limits` (`TIDE_PANEL_SCREEN=n` ogranicza do ekranu n). Skrypt bez wykonywania: `bash scripts/swap-limits-widget.sh --print`. Panel z zablokowanymi widżetami trzeba najpierw odblokować.

## Modele

Od PT-41 model lokalny pokazuje kafelek „AI” (wyżej), a kafelek „Tide · Modele” (`org.tide.models`) nie stoi już w docku: `make install` dalej instaluje pakiet (powrót to dopisanie go do `DOCK_RIGHT` w `scripts/dock.py`), a ponowna instalacja docka zdejmuje go z panelu — także wtedy, gdy dodasz go do docka ręcznie (`make install-dock` usuwa kafelki wycofane z docka, dopóki nie ma ich w `DOCK_LEFT` ani `DOCK_RIGHT`). Kafelek pokazuje stan domyślnego serwera modeli lokalnych z tide-daemon (`/Models`, `Status()` po sygnale `Changed`, [opis](docs/tide-daemon.md#modele-lokalne)): „● qwen3:8b · na karcie 100%” (model w pamięci; masło, gdy część jest na procesorze), „● gotowe”, „● pracuje: opis kart · 2 w kolejce” (teal), „● serwer nie odpowiada” (róż), bez usługi wyciszone „usługa nie działa”. Popup: karta na każdy serwer („Ten komputer” dla adresu lokalnego, adres, wersja, powód niedostępności, kolejka — także wywołania, które trwają jeszcze na starym adresie punktu po jego zmianie („Na starym adresie …: pracuje: raport”, pole `queue.<punkt>.other`), pobieranie, modele chmurowe Ollamy oznaczone „w chmurze Ollamy” („Pobrane: 6 modeli, w tym 1 w chmurze Ollamy”), modele w pamięci z udziałem karty i terminem zwolnienia, liczba pobranych), role z modelem i dostępnością, tryb raportu, „Odśwież” (`Refresh()`) i „Otwórz w Tide ↗”. Pola `report`, `pulls` (pobieranie po kluczu `"<punkt>/<model>"`) i `queue` są opcjonalne — bez nich kafelek ich nie pokazuje.

„Otwórz w Tide” woła wprost okno: `org.tide.Window /Window Show("models", "")`; gdy okno nie działa, uruchamia je jak menu (`kstart --application tide`) i ponawia `Show`. Dopóki okno nie ma widoku Modele, `Show` zwraca `false` — okno wychodzi na wierzch na swoim widoku (`Show("", "")`), a popup mówi, że widok przyjdzie z nową wersją okna.

```bash
make install-widget-models     # instalacja albo aktualizacja widżetu
make uninstall-widget-models   # usuwa tylko org.tide.models
/usr/bin/python3 widgets/preview_models.py   # kafelek i popup na przykładowych danych: build/preview/models-*.png
```

## System

Widżet „Tide · System” (`org.tide.system`) pokazuje w kafelku „System” trzy mierniki z procentem i cienkim paskiem („CPU 34%  RAM 61%  Dysk 88%”): procesor (teal), pamięć (lawenda) i pełniejszą z partycji / i /home (brzoskwinia). W popupie i w pasku bocznym jest wykres procesora z ostatniej minuty (próbka co 2 s), pamięć z trzema programami zajmującymi najwięcej, karta graficzna z VRAM i obie partycje z wolnym miejscem.

```bash
make install-widget-system     # instalacja albo aktualizacja widżetu
make uninstall-widget-system   # usuwa tylko org.tide.system
/usr/bin/python3 widgets/system/contents/code/sysinfo.py mounts / /home   # mapowanie partycji na czujniki
```

**Dane.** Liczby pochodzą z czujników usługi `ksystemstats` (moduł QML `org.kde.ksysguard.sensors`), tych samych co w Monitorze systemu: `cpu/all/usage`, `memory/physical/used` i `total`, `gpu/gpu0/usage`, `usedVram`, `totalVram` i `name`, `disk/<UUID>/used`, `total` i `free`. Procent pamięci i dysku liczymy z `used/total`. Bez pakietu `ksystemstats` widżet pokazuje „Zainstaluj pakiet ksystemstats” zamiast zer, a dyski bierze ze `statvfs`. Pakiet instalujemy tylko po `apt-get -s install ksystemstats`.

**Partycje.** Czujniki dysków mają w nazwie UUID systemu plików, nie punkt montowania. Mały pomocnik `contents/code/sysinfo.py`, uruchamiany z widżetu przez silnik `executable` Plasma5Support (co minutę), czyta `/proc/self/mountinfo`, znajduje partycję zawierającą `/` i `/home`, a jej UUID w `/dev/disk/by-uuid` — tak samo jak ksystemstats nazywa czujnik (bez UUID etykieta, potem nazwa urządzenia). Nic nie jest wpisane na sztywno, więc po zmianie partycji wystarczy restart Plasmy. Jeśli /home nie jest osobną partycją, widżet pokaże jedną.

**Procesy.** Ten sam pomocnik (`top 3`, co 4 s, tylko przy otwartym popupie) sumuje pamięć prywatną procesów (rezydentna minus współdzielona, czyli bez bibliotek dzielonych z innymi procesami) po nazwie programu z `argv[0]`, więc wszystkie procesy Firefoksa liczą się razem.

**Podpowiedź przy pełnym dysku.** Przy partycji zajętej powyżej 85% widżet pyta `tide-daemon` o obszar `/Worktrees` (`org.tide.Daemon.Worktrees.List`) i pisze „Powyżej 85%: worktrees zajmują X, z czego Y można zwolnić” — liczą się worktrees leżące na tej partycji (bez głównych katalogów repozytoriów), Y to te, które usunie sprzątanie. Gdy usługa nie ma tego obszaru albo nie działa, zostaje samo „Dysk prawie pełny”.

**Przyciski.** „Monitor systemu” (plasma-systemmonitor, zapasowo KSysGuard) i „Analiza dysku” (Filelight, otwiera najpełniejszą partycję) pojawiają się tylko wtedy, gdy aplikacja jest zainstalowana; startują przez `kstart --application`.

## Stream (widżety rdzenia Streamer)

Widżety aktywności Stream (PT-37, mock zaakceptowany 28.10.2026) czytają stan streamu z rdzenia Streamer
(`~/DevWorkspace/streamer/core`, usługa użytkownika `streamer-core`) przez D-Bus: usługa `org.streamer.Core`,
obiekt `/org/streamer/Core`, interfejs `org.streamer.Core1`. To świadomy wyjątek od „jednego demona” (decyzja 79):
stan streamu należy do rdzenia, dane pracy dalej idą tylko z tide-daemon.

- **Stream** (`org.tide.stream`, dock Stream): „Stream · Xbox” i „● poza anteną · ● OBS”, na żywo „Stream · Xbox ·
  scena Gra” i „● na żywo · ● nagrywa”. Kolor ostrzegawczy (`neutral` z palety) tylko przy wyciszonym mikrofonie na
  żywo („Stream · na żywo”, przekreślony mikrofon, „mikrofon wyciszony”), rozłączonym OBS w trakcie streamu
  i niedziałającym rdzeniu („rdzeń nie działa”, gdy usługi nie ma na szynie). Bez OBS i bez streamu kafelek mówi
  spokojnie „Granie · Xbox” (w Observer i Rozmowa „Stream · …”) i „bez streamu” (PT-83). Popup: karta Stream — tryb i kto go ustawił (automat albo ty),
  antena, nagrywanie, scena, OBS, moduły, „Wycisz mikrofon · Pause” (`mic.set` z docelowym stanem), „Panel streamera”
  (`http://127.0.0.1:8765/app/`) i dopisek, że start i koniec streamu są tylko w Panelu.
- **Profil gracza** (`org.tide.stream.profile`): bieżący profil z „▾”, popup z listą `profiles` (`profile.set`).
- **Observer** (`org.tide.stream.observer`): „Mascot Cup · BO3 · gra 2” i „A 1 : 0 B”; tylko w trybie Observer
  (w innym trybie i bez rdzenia `HiddenStatus`, panel go chowa). Kliknięcie otwiera Panel streamera.
- **Kamera** (`org.tide.stream.camera`, PT-82, decyzja 83): „Kamera · automat” albo „Kamera · ustawiłeś ty”
  i „● widoczna” albo „○ ukryta” (`core.modules.camera`). Kliknięcie od razu, bez popupu i potwierdzenia, wysyła
  `module.set {module: "camera", visible: …}` z odwróconą wartością (docelowy stan, nie przełączanie w ciemno);
  rdzeń chowa kamerę we wszystkich elementach scen zależnych od modułu, a zmiana trybu przywraca domyślną
  widoczność trybu. Bez koloru ostrzegawczego: przy rozłączonym OBS „bez OBS” (rdzeń zapamiętuje zmianę i ustawia
  OBS po połączeniu), bez rdzenia „rdzeń nie działa”, błąd komendy na chwilę zamiast wartości w `textSecondary`.
- **„?”** (`org.tide.stream.help`): ściąga ze skrótami Stream w popupie, nigdy stale na ekranie.
- **Konsola streamu** (`org.tide.stream.scenes`, pulpit lewego monitora, gdy nie ma desku): Sceny — podgląd bieżącej sceny z „NA ŻYWO”
  i przyciski scen trybu (`scene.set`; BRB tylko osobnym przyciskiem, sceny startu i końca na żywo wyłączone
  z powodem) — i pełna karta Stream: antena, OBS z nagrywaniem, profil gracza z listą, przełączniki modułów
  (`module.set`), „Wycisz mikrofon”, BRB (`brb.toggle`) i Panel streamera. Zrzut sceny odświeża się tylko w bieżącej
  aktywności i przy widocznym widżecie.
- **Czat Twitcha** (`org.tide.stream.chat`, pulpit lewego monitora, gdy nie ma desku): duża czcionka (1,5 × rozmiar z palety, ok.
  20 px), najnowsze na dole, alerty w linii, tylko do czytania; nick w pastelu palety innym niż kolory ostrzeżenia
  i błędu (`semantic.neutral`, `semantic.negative`).
- **Lewy monitor Stream** (`org.tide.stream.desk`, PT-83, decyzja 82): jeden widżet na cały pulpit lewego monitora
  z dwoma widokami. **Konsola streamu** to Sceny z kartą Stream i czat (te same komponenty co wyżej), **Granie** —
  profile graczy (aktywny wyróżniony, kliknięcie innego to `profile.set`; ranga, rating, forma, gdy rdzeń je ma),
  bilans dnia, „Mecz teraz” z pola `core.match` (Ty i przeciwnik: cywilizacja, ranga, rating, procent wygranych,
  forma) i ostatnie mecze aktywnego profilu. Widok wybiera automat: OBS połączony → konsola, OBS wyłączony →
  Granie, na żywo zawsze konsola. Przełącznik „Konsola streamu | Granie” na górze pokazuje, kto wybrał widok
  („automat · OBS wyłączony” albo „ustawiłeś ty · do zmiany OBS”); ręczny wybór obowiązuje do następnej zmiany stanu
  OBS i nie chowa konsoli na żywo.

**Rozmowa z rdzeniem.** Jedno `CoreSource` na widżet: `GetState()` przy starcie i po każdym pojawieniu się usługi na
szynie, potem tylko sygnał `Changed` (niesie cały widok w `state`), do tego odczyt zapasowy co 5 min. Gdy rdzeń
znika, widżety od razu mówią „rdzeń nie działa”; po jego powrocie same czytają stan od nowa. Komendy idą przez
`Execute` wyłącznie z listy `Stream.ALLOWED` (`mic.set`, `profile.set`, `module.set`, `scene.set`,
`brb.toggle`) — akcji krytycznych (start i koniec streamu, koniec nagrania, zmiana trybu na żywo, reset serii)
w widżetach nie ma.

**Czego rdzeń jeszcze nie daje.** Listy scen trybu, podglądu bieżącej sceny i czatu z alertami przez D-Bus,
a dla widoku Granie przeglądu wszystkich kont, ostatnich meczów, bilansu dnia i danych meczu w trybie PC (propozycja
pól w decyzji 82: `core.player_stats`, `core.recent_matches`, `core.today`). Konsola, czat i Granie pokazują wtedy
„jeszcze niedostępne w rdzeniu”, a `CoreSource` i `stream.js` mają już miejsce na te dane
(propozycja API w decyzji 79): `core.mode_scenes` (i opcjonalnie `obs.scenes`) w stanie, `GetSceneScreenshot(s)`,
`GetChat()` i sygnał `Chat(s)`, a pilnie `obs.mic`. Bez scen trybu konsola nie pokazuje przycisków scen, tylko
bieżącą scenę. Testy sprawdzają obie wersje na atrapie rdzenia (`widgets/tests/fake_kamp_core.py`, zawsze na
prywatnej szynie `dbus-run-session`).

**Instalacja.** `make install-widget-stream` instaluje osiem pakietów (też w `make install`), ale niczego nie stawia
na panelach ani pulpicie — rozmieszczenie robi wejście do aktywności Stream (niżej: „Aktywność Stream”). Kolory z palety, więc widżety działają
z Mist i z wariantem Streamer.

```bash
make install-widget-stream     # instalacja albo aktualizacja ośmiu pakietów Stream
make uninstall-widget-stream   # usuwa tylko pakiety org.tide.stream*
make preview-stream            # kafelki, popupy, konsola i Granie 1920×1080 na fałszywym stanie: build/preview/stream-*.png
make preview-stream PALETTE=theme/palette/streamer.json   # z wariantem Streamer — plik palety dostarcza PT-34
```

## Monitory PC ⇄ Xbox i skróty Stream

Dwie rzeczy z PT-38 (decyzja 81), obie w tide-daemon:

- **Wejście prawego monitora.** Kafelek „Monitory · PC → Xbox” (`org.tide.stream.monitors`, w docku Stream) otwiera
  potwierdzenie. **Xbox:** prawy monitor DP-2 przechodzi przez DDC/CI na wejście HDMI z passthrough HD60 X
  (`ddcutil setvcp 60 0x11`). **PC:** wraca na DisplayPort (`0x0f`). Układ ekranów w KWin się nie zmienia, więc
  dock Stream i konsola zostają na lewym monitorze. Skutek: w trybie Xbox kursor, nowe okno albo powiadomienie mogą
  trafić na prawy ekran, którego wtedy nie widać — kursor wraca ruchem w lewo. Po wyjściu ze Streamu monitor sam
  wraca na PC, więc praca zawsze ma oba ekrany. Bez `ddcutil` albo gdy monitor nie odpowiada na DDC, widżet prosi
  o przełączenie wejścia przyciskiem na monitorze. Kody wejść są w `daemon/tide_daemon/monitors.toml` (gniazdo
  HDMI z Xboxem do sprawdzenia: HDMI-1 to `0x11`, HDMI-2 to `0x12`). Bez skrótu klawiszowego.
- **Skróty tylko w Stream.** `Pause` — mikrofon (`kamp mic.toggle`), `Meta+Pause` — ekran prywatności
  (`kamp privacy.toggle`), `Meta+B` — BRB i powrót (`kamp brb.toggle`). Usługa rejestruje je w KGlobalAccel dopiero
  po wejściu do aktywności Stream i usuwa po wyjściu, więc w pracy klawisze działają jak dawniej. `Meta+B` jest na
  czas Streamu zabierane „Przełącz profil zasilania”, a po powrocie oddawane dokładnie (zapis w
  `$XDG_STATE_HOME/tide/stream-shortcuts.json`). `Meta+Shift+A` (Praca ⇄ Stream), `Meta+A` i `Meta+Q` bez zmian.

```bash
make install-widget-monitors   # pakiet kafelka (miejsce w docku Stream — układ paneli Stream)
make preview-monitors          # kafelek i potwierdzenie: build/preview/stream-monitors-*.png (paleta Streamer)
make monitors-status           # wejście prawego monitora według stanu usługi (tylko odczyt, bez DDC)
qdbus6 org.tide.Daemon /Monitors org.tide.Daemon.Monitors.SetLayout xbox   # to samo co przycisk (pc | xbox)
```

## Styl Plasmy i górne paski

Styl „Tide” zawiera tylko ramki o nowym kształcie: tło panelu (pasek z prefiksem `north` i promieniem paska, dock z prefiksem `south` i pasek boczny z prefiksem `west` z promieniem docka), wyskakujące okna z nagłówkiem i stopką oraz podpowiedzi — w wersjach zwykłej, `translucent/`, `solid/` i `opaque/`, jak w Breeze. Resztę Plasma bierze z Breeze, bo „default” jest zawsze ostatnim motywem zapasowym (dodatkowo `FallbackTheme=default` w `plasmarc`). Kolory nadal pochodzą ze schematu: styl nie ma pliku `colors`, a wypełnienia mają klasy `ColorScheme-*`. Licencja i pochodzenie: `NOTICE` w katalogu stylu (LGPL-2.0-or-later, jak Breeze).

```bash
make preview-plasma-style                                      # podgląd w build/preview, bez instalowania
/usr/bin/python3 theme/plasma/ksvg_check.py theme/palette/mist.json build/ksvg.png   # prawdziwe KSvg, poza ekranem
make show-panels                                               # skrypty plasmashell, które uruchomi instalacja
```

Górne paski (`scripts/panels.py`): na głównym monitorze (DP-2) istniejący górny panel staje się pływającą pigułką — menu z logo Tide (ikona `tide-logo` w hicolor), globalne menu, zegar z datą pośrodku i zasobnik. Na lewym monitorze (HDMI-A-1) powstaje smukły pasek z globalnym menu i zegarem. Oba menu pokazują tylko okna ze swojego monitora. Monitory rozpoznajemy po złączach (`TIDE_MAIN_OUTPUT`, `TIDE_SIDE_OUTPUT`), pasek boczny po znaczniku `[Tide] role=side-bar` w konfiguracji panelu. Przed pierwszą zmianą układ paneli trafia do `~/.local/state/tide/panels-previous.json` (razem z plikami `.desktop` widżetów „Ikona”), a `make uninstall` przywraca z niego główny pasek i usuwa boczny. Pełny układ jest też w kopii zapasowej robionej przez `make install`.

### Globalne menu

Działa przez moduł kded `appmenu` (instalacja włącza go, jeśli był wyłączony) i rejestr `com.canonical.AppMenu.Registrar`, który pojawia się, gdy na panelu jest widżet globalnego menu. Aplikacje sprawdzają to przy starcie, więc te uruchomione przed instalacją trzeba zrestartować.

| Aplikacja | Globalne menu |
| --- | --- |
| Aplikacje KDE/Qt (Dolphin, Kate, Konsole, Okular, Ustawienia systemowe) | tak — menu przez D-Bus (dbusmenu) i protokół appmenu KWin |
| Rider i inne IDE JetBrains | prawdopodobnie tak, dopóki działają przez XWayland (domyślnie): IDE rejestruje menu w `com.canonical.AppMenu.Registrar`. Z natywnym Waylandem (`-Dawt.toolkit.name=WLToolkit`) menu zostaje w oknie |
| Firefox | nie — rysuje własny pasek menu, nie eksportuje go; menu (Alt) zostaje w oknie |
| Aplikacje GTK 3 | zależnie od aplikacji: przez `appmenu-gtk-module` (zainstalowany) i `gmenudbusmenuproxy`, pewnie tylko pod XWayland |

Do sprawdzenia po instalacji: Dolphin, Rider i Firefox na obu monitorach.

## Pasek boczny

Na lewym monitorze (HDMI-A-1) przy lewej, zewnętrznej krawędzi jest chowany pasek boczny Tide (`scripts/sidebar.py`, cel `install-sidebar` na końcu `make install`): pionowy, pływający panel szeroki na `size.sidebar` (420 px), od dołu smukłego paska do dołu ekranu. Jest widoczny, dopóki nic go nie zasłania („unikaj okien”, nie zabiera oknom miejsca); gdy najedzie na niego okno, chowa się i wysuwa, gdy mysz dotknie lewej krawędzi ekranu albo po **Meta+S** (po skrócie: na 8 s bez myszy nad paskiem albo do drugiego Meta+S). Od góry: nagłówek „Tide” ze skrótem i cztery osobne kafelki — Praca (karty tablicy i sesje bez karty, jedna linia na pozycję, najpierw to, co czeka, z „Przejdź”, najwyżej 5 wierszy i „+N więcej” z pełną listą obok paska, „Tablica” i „Sesje”), system (CPU, RAM i dysk z paskami, mała historia procesora, karta graficzna w jednej linii, partycje), worktrees („● 2 w pracy · ● 0 do sprzątnięcia” i „Otwórz okno”) i Limity AI (GPT i Claude: niższy limit z paskiem, resetem i prognozą z tide-daemon). Wolne miejsce zostaje na dole (rozciągany odstęp na końcu panelu), a całość mieści się w ok. 1040 px bez przewijania. Podgląd bez Plasmy: `make preview-widgets` (`build/preview/sidebar-*.png`, z kafelkiem Praca: `work-sidebar-whole.png`).

```bash
make install-widget-sidebar install-sidebar   # sam pasek (widżety Tide muszą być zainstalowane)
make show-sidebar                             # skrypt plasmashell, który uruchomi instalacja
make sidebar-preview                          # zagnieżdżona sesja: schowany, wysunięty skrótem i znów schowany → build/sidebar
make uninstall-sidebar                        # usuwa panel i zwalnia skrót
```

- **Kafelki paska.** Widżety w pasku mają w konfiguracji `sidebarMode=true` (`[Configuration][General]`), więc Plasma pokazuje od razu ich pełną reprezentację zamiast kafelka — w trybie paska to zwarty kafelek (`SidebarWork`, `SidebarSystem`, `SidebarWorktrees`, `SidebarLimits` na wspólnym `SidebarTile`). Kafelki w docku i ich popupy się nie zmieniają. Praca w pasku nie zgłasza „wymaga uwagi”, bo chowany panel nie chowałby się wtedy wcale — o czekającym agencie mówią dock i powiadomienie.
- **Meta+S.** To globalny skrót widżetu-nagłówka `org.tide.sidebar` (w KGlobalAccel: komponent `plasmashell`, akcja `activate widget <id>`), zmieniany w jego ustawieniach (prawy przycisk na nagłówku → Skróty klawiszowe). Gdy Meta+S ma już inna akcja, instalator to wypisze i nie nada skrótu. Szczegóły w decyzji 34.
- **Po pierwszej instalacji** Plasma wczyta nowy kod widżetów (tryb paska) dopiero po `systemctl --user restart plasma-plasmashell` albo ponownym zalogowaniu.
- Panel ma znacznik `[Tide] role=sidebar`; ponowna instalacja używa tego samego panelu i tych samych widżetów, a szerokość trafia też do `plasmashellrc` (jak grubość górnych pasków).

## Worktrees

```bash
make install-daemon install-widget-worktrees install-tide-window
make preview-tide-window   # zrzuty widoków, w tym build/tide-window-worktrees.png (tylko odczyt)
```

Kafelek „Tide · Worktrees” w docku pokazuje, ile worktrees można sprzątnąć bez ryzyka i ile to GB (mięta), a pod spodem ile jest w pracy (teal). Kliknięcie otwiera okno **Tide** na widoku Worktrees (własna ikona i nazwa w pasku zadań, także w menu aplikacji). Okno dzieli worktrees na trzy grupy:

- **W pracy** (teal) — pracuje w nim agent albo coś zmieniło się w ostatniej dobie i praca nie jest jeszcze skończona (nie scalona, zmiany albo świeży worktree bez własnych commitów); otwieranie w Konsoli albo Riderze, a te bez działającego agenta można też zaznaczyć do usunięcia (jak „Do decyzji”),
- **Do sprzątnięcia** (mięta) — scalone z **lokalną** gałęzią główną (master, main albo develop, bez łączenia z serwerem), bez zmian i bez agenta. Worktree z własnymi commitami (widać je w reflogu gałęzi) trafia tu od razu po scaleniu, bez czekania doby. Każdy ma pole wyboru; „Usuń zaznaczone” najpierw wymienia dokładnie katalogi i gałęzie, które znikną. Usuwanie bez `--force`, gałąź tylko przez `git branch -d`, a usługa sprawdza każdy worktree jeszcze raz tuż przed usunięciem,
- **Do decyzji** (masło) — stoją od ponad doby, ale mają niescalone commity albo zmiany; widać dlaczego („4 commity · 3 zmiany”). Można je otworzyć albo zaznaczyć (pole wyboru robi się czerwone). Starsze niż 30 dni są wyciszone.

Gdy wśród zaznaczonych jest worktree spoza „Do sprzątnięcia”, potwierdzenie jest mocniejsze: dla każdego wymienia, co przepadnie (niezacommitowane zmiany, commity poza master i ile z nich nie ma kopii na serwerze ani w innej gałęzi, los gałęzi, powiązaną sesję, ostatnią zmianę), ma pole „Usuń też gałąź (N niescalonych commitów przepadnie)” i czerwony przycisk „Usuń mimo to”. Usługa usuwa wtedy z `git worktree remove --force`, a niescaloną gałąź zostawia, chyba że pole jest zaznaczone (`git branch -D`); wynik mówi, przy których gałąź została. Nigdy — także w ten sposób — nie usuwa głównego katalogu repozytorium, worktree na gałęzi głównej, zablokowanego, tego, w którym pracuje agent (najpierw zamknij sesję), ani tego, w którego katalogu leżą inne worktrees (np. `.claude/worktrees/` — zniknęłyby razem z nim; najpierw usuń te w środku). Podgląd potwierdzenia bez usługi: `tide-worktrees.py --data plik.json --select ŚCIEŻKA --confirm --grab zrzut.png`.

Wiersz mówi, co to za worktree: narzędzie (Codex, Claude — z sesji, która w nim teraz pracuje, inaczej z ostatniej), tytuł z nazwy gałęzi bez prefiksu (`codex/summary-tabs-03-version-axis` → „summary-tabs 03 · version axis”), temat ostatniego commita albo tytuł powiązanej sesji agenta, ostatnią aktywność i rozmiar. Worktrees na gałęzi głównej są w zwiniętej sekcji „Gałęzie główne” i nigdy nie są sprzątane, a wpisy po ręcznie usuniętych katalogach porządkuje przycisk `git worktree prune` przy repozytorium. Katalog projektów to `~/DevWorkspace` (zmiana: `TIDE_WORKSPACE` w środowisku usługi albo `--workspace`).

## Motyw globalny i ekran blokady

Motyw globalny „Tide” (`org.tide.desktop`) spina w Ustawieniach systemowych to, co instalują pozostałe części: schemat TideMist, styl Plasmy Tide, dekorację Aurorae Tide (przyciski po prawej: minimalizuj, maksymalizuj, zamknij), ikony Tide, kursor Nordic-cursors i czcionki Geist. Nie ma w nim skryptów układu ani tapety — panele i tapety ustawiają `scripts/panels.py` i `scripts/wallpaper.py`.

`make install` (cel `install-lookandfeel`) kopiuje motyw do `~/.local/share/plasma/look-and-feel/org.tide.desktop` i zapisuje tylko wybór motywu (`kdeglobals [KDE] LookAndFeelPackage`, wyłączona automatyczna zmiana motywu) oraz tapetę ekranu blokady (obraz Tide głównego monitora w `kscreenlockerrc`). **Nie wywołuje `plasma-apply-lookandfeel`** i nie dotyka paneli. Poprzednie wartości są w `~/.local/state/tide/lookandfeel-previous.json`, `make uninstall` je przywraca.

W Ustawieniach systemowych → Motyw globalny można kliknąć „Tide”, ale **nie zaznaczać „Układ pulpitu i okien”**: Plasma kasuje wtedy całą konfigurację paneli (`plasma-org.kde.plasma.desktop-appletsrc`) i ładuje układ Breeze. Ratunek: `make restore NAME=<kopia>` i ponowne `make install`.

**Ekran blokady.** W Plasmie 6.8 greeter (`kscreenlocker_greet`) bierze ekran blokady wyłącznie z pakietu powłoki (`plasma/shells/<powłoka>/contents/lockscreen`), a powłokę wybiera tak samo jak plasmashell (`plasmashellrc [Shell] ShellPackage`, domyślnie `org.kde.plasma.desktop`). Katalog `lockscreen/` w motywie globalnym jest ignorowany, a klucz `kscreenlockerrc [Greeter] Theme` nie istnieje. Dlatego ekran Tide powstaje jako pakiet powłoki `org.tide.desktop` z samym `lockscreen/` (do trybu testowego), a prawdziwym ekranem blokady staje się przez **kopię powłoki** (decyzja 27, wariant a): `make install` kopiuje cały systemowy `/usr/share/plasma/shells/org.kde.plasma.desktop` do `~/.local/share/plasma/shells/org.kde.plasma.desktop` i podmienia w nim tylko `contents/lockscreen` na ekran Tide. Pakiet w katalogu domowym wygrywa z systemowym, więc greeter bierze z niego ekran Tide, a plasmashell resztę — identyczną z systemem. Powłoka się nie zmienia (`ShellPackage` zostaje `org.kde.plasma.desktop`), więc panele i plik `plasma-org.kde.plasma.desktop-appletsrc` też nie.

```bash
make lockscreen-screenshot   # prawdziwy greeter w zagnieżdżonym KWin: build/lockscreen-sleep.png (bezczynność),
                             # build/lockscreen.png (po „klawiszu”), to samo 1920×1080 i dwa ekrany (lockscreen-dual*)
make lockscreen-test         # greeter w trybie testowym jako okno (XWayland); hasło zamyka okno
make lockscreen-shell-check  # kopia powłoki w zagnieżdżonej sesji: plasmashell i greeter → build/lockscreen-shell-check
make shell-sync-status       # czy kopia powłoki jest włączona i jak skończyła się ostatnia synchronizacja
make lookandfeel-build       # sam motyw globalny w build/lookandfeel
```

**Synchronizacja kopii.** Po aktualizacji Plasmy plasmashell czytałby starą kopię, dlatego jednostka użytkownika `tide-shell-sync.service` (oneshot) uruchamia przy każdym logowaniu `~/.local/share/tide/shell-sync.py` — kopię skryptu `scripts/shell_sync.py`. Jednostka jest `WantedBy=plasma-workspace.target` i `Before=plasma-plasmashell.service`: startplasma uruchamia `plasma-workspace-wayland.target`, które wymaga `plasma-workspace.target`, a ono `plasma-core.target` z plasmashell, więc to jedna transakcja systemd i plasmashell czeka na koniec synchronizacji (sam plasmashell nie jest uporządkowany po `graphical-session-pre.target`, stąd jawne `Before=`). Skrypt liczy odcisk SHA-256 plików pakietu systemowego i ekranu Tide; gdy zgadza się z odciskiem zapisanym w kopii (`tide-shell-copy.json`), kończy się po ok. 40 ms (obrazy kota w ekranie blokady dokładają do liczenia odcisku ok. 15 ms). Gdy nie, składa nową kopię poza `plasma/shells/`, sprawdza ją prawdziwym greeterem (`kscreenlocker_greet --testing --shell`, poza ekranem, bez magistrali sesji, ok. 2 s) i dopiero wtedy podmienia. Gdy ekran Tide się nie wczyta, **usuwa kopię w całości** i zostaje ekran Plasmy z systemu; kolejna próba dopiero po następnej zmianie (aktualizacja Plasmy albo `make install`). Stan ostatniej decyzji: `~/.local/state/tide/shell-sync.json`, log: `journalctl --user -u tide-shell-sync`. `make uninstall` wyłącza jednostkę i usuwa ją razem z kopią, skryptem i stanem.

**Ryzyka kopii powłoki.**

- Plasmashell czyta z kopii cały pakiet powłoki, nie tylko ekran blokady. Gdyby kopia była niezgodna z systemem (np. Plasma zaktualizowana bez ponownego logowania, a potem restart samego plasmashell), pulpit może działać na plikach starszej wersji do następnego logowania. Synchronizacja działa tylko przy logowaniu; po aktualizacji Plasmy najlepiej się wylogować.
- Sesja uruchamiana bez systemd (`startkderc [General] systemdBoot=false`) nie uruchomi jednostki — wtedy kopia nie odświeży się sama.
- Sprawdzenie w greeterze wykrywa błędy wczytania QML, ale nie błędy, które wyjdą dopiero przy wpisywaniu hasła. Gdy ekran Tide się zepsuje w trakcie blokady, greeter sam przechodzi na awaryjny ekran KDE.
- Po zmianie odcisku synchronizacja opóźnia start plasmashell o ok. 2 s (raz po każdej aktualizacji Plasmy). Gdy coś pójdzie nie tak, jednostka nie blokuje plasmashell (bez `Requires=`), a przy nieoczekiwanym błędzie skrypt usuwa kopię.
- Kopii, której nie zrobił Tide (bez `tide-shell-copy.json`), skrypt nie rusza.
- Kopia trafia też do kopii zapasowych (`.local/share/plasma`). Przywrócona stara kopia ma inny odcisk, więc przy następnym logowaniu jest odbudowywana.

**Ratunek.** Gdy ekran blokady albo pulpit nie działa przez kopię powłoki: usunięcie katalogu przywraca ekran i powłokę Plasmy z systemu, a synchronizacja respektuje to usunięcie (nie odtworzy kopii aż do kolejnego `make install`).

```bash
rm -r ~/.local/share/plasma/shells/org.kde.plasma.desktop
```

Jeśli ekran jest zablokowany i nie da się odblokować: Ctrl+Alt+F3, zalogowanie w konsoli tekstowej, polecenie wyżej, potem `loginctl unlock-session <numer>` (numer sesji graficznej z `loginctl list-sessions`) i powrót Ctrl+Alt+F2 (albo F1). Pełne wyłączenie: `make uninstall-lookandfeel` albo `systemctl --user disable tide-shell-sync.service`.

**Kot na ekranie blokady (wariant tapety `cat`).** Ekran Tide rysuje tło sam (`CatBackdrop.qml`), osobno na każdym monitorze, w jego kadrze (obrazy jadą w pakiecie: `tide/backdrop/awake|sleep/<złącze>.png`, kadry z układu monitorów w chwili `make install`). Kot śpi od chwili zablokowania i otwiera oczy tylko wtedy, gdy ktoś naprawdę używa ekranu: ruch myszy, kliknięcie, klawisz, pisanie albo kasowanie w polu hasła (zdarzenia samego ekranu blokady, bez globalnego przechwytywania klawiatury) budzą go przenikaniem w 650 ms (`lockscreen.wakeMs`). Po 3 s bez działania (`lockscreen.dozeAfterMs`) zasypia — wolniej i łagodniej, w 1100 ms (`lockscreen.sleepMs`) — nawet gdy formularz jest dalej widoczny albo w polu zostało hasło. Nie zasypia, póki trwa sprawdzanie hasła i póki widać komunikat o nieudanym odblokowaniu; gdy formularz zniknie (10 s bez działania, Escape), śpi od razu. Greeter ma jeden silnik QML dla wszystkich monitorów, więc kot budzi się na obu ekranach naraz. Przy wyłączonych animacjach zmiana jest natychmiastowa. Przyciemnienie jest jedno — nakładka `lockscreen.dim` na surowych obrazach — a kota nie rozmywamy. Wersja senna to ten sam obraz co czuwająca, z podmienionymi tylko okolicami oczu, więc przy przenikaniu zmieniają się wyłącznie oczy, a tło i sylwetka stoją w miejscu (`sleepRegistration` w `theme/wallpaper/assets/cat/manifest.json` wynosi [0, 0]; mechanizm korekty zostaje dla przyszłych, osobnych renderów). Pusty komunikat PAM, który greeter Plasmy dostaje zaraz po zablokowaniu, nie pokazuje formularza, więc kot nie budzi się sam na starcie.

**Warianty ekranu blokady.** Kopia powłoki ma obok ekranu Tide ekran aktywności Stream (paleta Streamer, obrazy grafika; pakiet źródłowy w `~/.local/share/tide/lockscreens/streamer`): aktywny w `contents/lockscreen`, pozostałe w `contents/lockscreen-variants/`, każdy sprawdzony greeterem przy składaniu kopii. `make install` zostawia wybrany wariant (na nowym systemie Tide). Przełączenie to jedna atomowa zamiana katalogów, bez przebudowy, od następnej blokady: `scripts/lookandfeel.py` (`lock_variants()`, `lock_variant()`, `apply_lock_variant("streamer")`) albo `python3 ~/.local/share/tide/shell-sync.py variant [mist|streamer]` (decyzja 77). Składanie kopii trwa przez to dłużej o 2–3 s za każdy dodatkowy wariant, bo każdy przechodzi sprawdzenie w greeterze: jednorazowo przy pierwszym `make install` po scaleniu (nowy format kopii) i przy pierwszym logowaniu po aktualizacji Plasmy; zwykłe logowanie dalej kosztuje ok. 40 ms. Podgląd: `make lockscreen-screenshot PALETTE=streamer`.

Wygląd z makiety: przyciemniona tapeta (przy plamach rozmyta, gdy widać pole hasła), zegar Geist 96 px i data („poniedziałek, 26 października”) w górnej części, logo „trzy fale”, nazwa użytkownika i pole hasła na karcie z tealową obwódką, na dole klawiatura, uśpienie i zmiana użytkownika jako same ikony. Linia „2 agenci pracują · 1 czeka” pochodzi z tide-daemon przez D-Bus sesji (greeter działa jako użytkownik i dziedziczy magistralę po KWin); pokazuje tylko liczby, bez nazw projektów, i znika, gdy usługi nie ma albo nikt nie pracuje. Gdy QML ekranu Tide się nie wczyta, greeter sam pokazuje awaryjny ekran KDE (sprawdzane w testach). Licencja i pochodzenie plików: `NOTICE` w pakiecie (GPL-2.0-or-later, jak ekran Plasmy).

## Ekran ładowania

Po zalogowaniu `ksplashqml` pokazuje ekran Tide z motywu globalnego (`contents/splash`): tapeta przyciemniona kolorem pulpitu (przyciemnienie `splash.dim` liczone przy składaniu pakietu, jedyne — QML nic już nie nakłada), w wariancie `cat` kot z otwartymi oczami w kadrze każdego monitora (`images/background-<złącze>.jpg`), pośrodku pełne logo „trzy fale”, pod nim cienki tealowy pasek postępu (wymiary z sekcji `splash` palety, promień `radius.small` ograniczony do połowy wysokości paska), logo delikatnie „oddycha”, a całość wchodzi krótkim przenikaniem (`splash.fadeMs`, 400 ms). QML to przerobiony ekran Breeze z tym samym API etapów: ksplashqml ustawia `stage` na 1…6 i kończy pracę po szóstym, więc pasek pokazuje `stage / 6`. Gdy w Ustawieniach wyłączono animacje, nic nie pulsuje, a ekran pojawia się od razu. Nie ma sztucznego czekania (np. 1,2 s z demonstracji) — postęp idzie wyłącznie za etapami. Ekran nie wydłuża startu: ksplashqml znika, gdy sesja jest gotowa, a obrazy wczytują się asynchronicznie.

`make install` zapisuje `ksplashrc [KSplash] Theme=org.tide.desktop` (poprzednia wartość w `lookandfeel-previous.json`), `make uninstall` przywraca poprzedni ekran, jeśli nadal wybrany jest Tide. Ekran działa też w podglądzie Ustawień systemowych → Ekran powitalny.

```bash
make splash-preview   # prawdziwy ksplashqml --test w zagnieżdżonym KWinie → build/splash/splash-*.png
```

Licencja: `NOTICE` w motywie (GPL-2.0-or-later, jak ekran Breeze, z którego pochodzi `Splash.qml`).

## Terminal

Konsola, prompt basha i Claude Code w kolorach Tide (#26), wszystko generowane z palety (sekcja `terminal`: rozmiar czcionki, marginesy, znak wodny). Podgląd bez dotykania bieżącej sesji — prawdziwa Konsole w zagnieżdżonym KWinie z tymczasowym domem:

```bash
make terminal-preview      # build/terminal/preview/konsole-agent.png i konsole-waiting.png
make terminal-themes       # same pliki promptu i motywów narzędzi w build/terminal
```

| Cel | Co zmienia | Cofnięcie |
| --- | --- | --- |
| `make install-konsole` (część `make install`) | profil „Tide” (Geist Mono, marginesy 18 px, środek, bez paska przewijania, tytuł karty z powłoki `%w`), kolorystyka TideMist (krycie `opacity.panel` z rozmyciem, trzy fale w prawym dolnym rogu), `konsolerc`: arkusz stylów paska kart, wyłączony pasek menu | `make uninstall-konsole` — poprzednie wartości kluczy `konsolerc` |
| `make install-terminal` (część `make install`) | oznaczony blok w `~/.bashrc` (kopia w `~/.local/state/tide/backups`), który wczytuje `~/.local/share/tide/terminal/tide.bash`; motywy narzędzi tylko dla zainstalowanych | `make uninstall-terminal` — blok znika, `~/.bashrc` wraca do treści sprzed instalacji |
| `make install-claude-statusline` (osobno) | tylko klucz `statusLine` w `~/.claude/settings.json` (kopia pliku w `~/.local/state/tide/backups`) | `make uninstall-claude-statusline` (też w `make uninstall`) — poprzednia wartość albo brak klucza |

**Pasek kart.** Pigułki jak kafelki Tide (Geist, tło `card`, promień `tile`, aktywna karta z obwódką teal) z arkusza stylów, który Konsole czyta z `konsolerc [TabBar] TabBarUseUserStyleSheet` / `TabBarUserStyleSheetFile` (klucze sprawdzone w Konsole 26.08 i na prawdziwej Konsoli w podglądzie). Karty z agentem oznacza tide-daemon: „● projekt · gałąź” (pracuje, pasek teal pod tytułem), „◆ projekt · gałąź · czeka” (pasek brzoskwiniowy), „○ projekt · gałąź” (skończył, bez koloru). Pozostałe karty pokazują tytuł z promptu: „projekt · gałąź”. Ograniczenia Konsoli: arkusz stylów nie ma selektora pojedynczej karty, więc kropka i słowo „czeka” nie mogą mieć własnego koloru ani karta tła w kolorze stanu — kolor stanu to pasek, który Konsole rysuje pod tytułem (`setTabColor`); arkusz układa pigułkę tak, żeby pasek wypadał pod tekstem. Przezroczyste jest tylko pole terminala (pasek kart i pasek narzędzi mają kolor okna). Paski narzędzi („Nowa karta”, „Podziel widok”) wyłącza się w Konsoli: Ustawienia → Pokaż paski narzędzi — Konsole zapisuje to w binarnym stanie okna, którego nie generujemy.

**Prompt.** Czysty bash (bez starship): chipy projektu (nazwa głównego repozytorium także w worktree), gałęzi, „worktree”, liczby zmian, przed/za i stanu agenta z tide-daemon („● claude pracuje”, „◆ codex czeka”, „○ claude skończył”), godzina po prawej, druga linia „›” (róż po błędzie). Gałąź i worktree czytane z plików `.git`, jedynym procesem jest `git status` z limitem 22 ms (`TIDE_PROMPT_GIT_TIMEOUT`), więc znak zachęty mieści się w 30 ms (zmierzone: 7–20 ms mediany w repozytoriach z `~/DevWorkspace`); gdy git nie zdąży, prompt pokazuje ostatni wynik tego repozytorium albo „±?”. Stan agenta pochodzi z pliku `~/.cache/tide/agents-by-cwd.tsv`, który pisze tide-daemon — bez D-Bus przy każdym znaku zachęty. Geist Mono nie ma zaokrąglonych końcówek powerline, więc chipy to tło z odstępem. `TIDE_PROMPT=off` w `~/.bashrc` przed blokiem wyłącza sam prompt (kolory narzędzi zostają).

**Pasek statusu Claude Code.** `projekt · gałąź · [worktree] · model · sesja ━━━ 63% · tydzień ━━━ 89% · starczy do resetu · kontekst 41%` z JSON-a, który Claude Code podaje skryptowi (`rate_limits`, `context_window`, `workspace`, `model`). Limity pokazują, ile zostało (kolory jak w widżecie Limity AI). Gdy Claude Code nie podaje limitów, skrypt pyta QuotaApp (`GET /status`, najwyżej co 60 s, wynik bez tokenu w `~/.cache/tide/claude-limits.json`). Prognoza („starczy do resetu”, „koniec ok. 17:40” w brzoskwini) pochodzi z tide-daemon (`/Limits Forecast()`, #24), pytanego przez `busctl` najwyżej co 60 s i bez uruchamiania usługi; bez usługi albo prognozy — z tempa zużycia w bieżącym oknie limitu. Codex nie ma polecenia paska statusu (tylko wbudowane pozycje `/statusline` i wybór motywu składni), więc go nie zmieniamy.

**Narzędzia.** `LS_COLORS` i kolory fzf są w `tide.bash`. Motywy bat (`Tide.tmTheme` + `bat cache --build`), delty (cecha „tide” przez `include.path` w `~/.gitconfig`), btop (`~/.config/btop/themes/tide.theme`) i lazygit (`LG_CONFIG_FILE`) instalują się tylko wtedy, gdy narzędzie jest zainstalowane. Żadnego z nich nie ma teraz w systemie. W repozytoriach Ubuntu 24.04 są btop 1.3.0, fzf 0.44.1, bat 0.24.0 (polecenie `batcat`) i git-delta 0.16.5; symulacja dokłada tylko `libgit2-1.7` i `libhttp-parser2.9`, bez Qt i KDE Frameworks. lazygit w repozytoriach nie ma.

```bash
apt-get -s install btop fzf bat git-delta   # najpierw symulacja
sudo apt install btop fzf bat git-delta && make install-terminal
```

## Vivaldi

Vivaldi to główna przeglądarka. `make install-vivaldi` (część `make install`) ubiera w Tide wszystkie profile naraz — Vivaldi musi być wtedy zamknięte, bo przy wyjściu nadpisuje swoje ustawienia (instalator odmawia, gdy przeglądarka działa):

- **Motyw profilu.** Grafit Mist (pasek adresu w kolorze paska tytułu, panele i strona startowa w kolorze okna, tekst `text.primary`, promień `small`), a pastel profilu na pasku kart (ciemny odcień, `tabBarTint`) i w podświetleniach — okna profili odróżnia się na pierwszy rzut oka. Tomek: teal, Tomek-Firma: błękit, lakomytomaszdev: mięta, Streamer: brzoskwinia (`vivaldi.profiles` w palecie; nowy profil dostaje kolejny pastel z `otherColors`). Kolor strony nie przebarwia paska (`accentFromPage` wyłączone). Motyw jest ustawiony także w harmonogramie „jak w systemie” (domyślnym), więc jasny i ciemny tryb Plasmy dają ten sam motyw.
- **Okno prywatne.** Osobny motyw „Tide Mist · prywatne”: całe okno lekko lawendowe (`privateTint`), lawendowe podświetlenia.
- **Interfejs.** Arkusz `~/.local/share/tide/vivaldi/css/tide.css` przez eksperyment „Allow CSS modifications” (`vivaldi-css-mods` w `Local State`, katalog w Ustawienia → Wygląd): Geist w całym interfejsie i przyciski okna jak w dekoracji Tide — pastelowe kółka masło, mięta, róż, znak po najechaniu (przywracanie w zmaksymalizowanym oknie), szare w nieaktywnym.
- **Awatary profili** zostają takie, jakie ustawisz w Vivaldi — instalator ich nie zmienia.
- **Ikona.** Stonowany glif jak przy innych programach: znak Vivaldi z Simple Icons w różu na ciemnoróżowym kafelku (`make install-icons`).

Przed zmianą instalator kopiuje `Local State` i `Preferences` każdego profilu do `~/.local/state/tide/vivaldi/backup-<czas>/`, a poprzednie wartości zmienianych kluczy zapisuje przy pierwszej instalacji. `make uninstall-vivaldi` (też przy zamkniętym Vivaldi) przywraca poprzednie motywy i flagę, usuwa motywy Tide z listy i pliki z `~/.local/share/tide/vivaldi`. Motywy Tide można potem zmieniać w Ustawienia → Motywy jak każdy inny; ponowne `make install-vivaldi` wybiera je z powrotem.

```bash
make preview-vivaldi       # prawdziwe Vivaldi w zagnieżdżonym KWinie, dwa profile i okno prywatne: build/vivaldi/*.png
```

## Kopie zapasowe

Przed każdą zmianą wyglądu powstaje kopia w `~/.local/share/plasma-tide/backups`.

```bash
make backup NAME=przed-zmiana   # nowa kopia
make backups                    # lista kopii
make restore NAME=przed-tide    # powrót do wyglądu sprzed projektu
```

`restore` sam robi najpierw kopię bieżącego stanu, więc przywracanie też da się cofnąć. Po przywróceniu warto się wylogować i zalogować ponownie.

## Tapeta

Rodzaj tapety wybiera `wallpaper.variant` w `theme/palette/mist.json`:

- **`cat`** (Mist, domyślnie): ilustracja kota z `theme/wallpaper/assets/cat` (opis, pochodzenie i sumy: `README.md` w tym katalogu). Na pulpicie kot zamyka oczy, gdy nikt nie używa komputera (niżej: „Kot na pulpicie”), na ekranie blokady śpi, dopóki ktoś nie używa ekranu. Dla układu, pod który przygotowano obrazy (HDMI-A-1 1920×1080 po lewej, DP-2 2560×1440 po prawej, wyrównane do góry), instalacja kopiuje gotowe pliki na monitor bez obróbki. Dla innego układu kadry są przeliczane z panoramy 4480×1440: jedna skala w obu osiach dla całego płótna wszystkich monitorów i przycięcie wokół kota, bez rozciągania pojedynczych ekranów. Powiększenie ponad 4480×1440 to zwykła interpolacja — szczegółów nie przybywa.
- **`blobs`**: tapeta rysowana z palety (sekcja `wallpaper`: kolory plam, ich położenie, krycie, ziarno i stałe ziarno losowania). Generator rysuje jedno płótno obejmujące wszystkie monitory w ich prawdziwym położeniu i tnie je na ekrany, więc plamy przechodzą płynnie z lewego monitora na prawy. Delikatne ziarno z ditheringiem zapobiega pasmom na gradientach.

Ten sam wariant (i te same kadry) dostają ekran blokady, ekran ładowania i ekran logowania. Aktywność Stream ma paletę Streamer z wariantem `pictures`: osobny obraz grafika na każdy monitor (pulpit, blokada, blokada w bezczynności), bez wspólnego płótna; `make install` zapisuje te obrazy obok tapety Mist (`wallpapers/Tide/pictures/`), a nakłada tylko paletę z `$TIDE_PALETTE` (domyślnie Mist). Porównanie obu wariantów bez instalowania: `make palette-compare` (`build/compare/`), podgląd jednego: `make wallpaper-preview PALETTE=streamer` (decyzja 77). Ponowne `make install` z wariantem `cat` zostawia kota — plamy powstają tylko po zmianie wariantu na `blobs`.

`make install` czyta układ monitorów z `kscreen-doctor -j` (gdy nie odpowiada, bierze znany układ), zapisuje po jednym obrazie na monitor w `~/.local/share/wallpapers/Tide/<wariant>/<złącze>.png` i ustawia je na pulpitach bieżącej aktywności (przy kocie przez wtyczkę `org.tide.cat`, która ma w sobie oba stany kota dla tego układu); pulpity innych aktywności dostają swoją tapetę przy przełączeniu (niżej: „Wygląd aktywności”). Każdy wariant ma osobny katalog, więc po zmianie wariantu zmienia się adres obrazu i Plasma na pewno wczyta nowy; obrazy poprzedniego wariantu (i starsze `wallpapers/Tide/<złącze>.png`) są usuwane, jeśli żaden pulpit już ich nie pokazuje. Po zmianie monitorów wystarczy ponowne `make install`. Potrzebne są numpy i Pillow, które w KDE neon ma systemowy `/usr/bin/python3`.

```bash
make wallpaper-preview          # same obrazy w build/wallpaper (przy kocie także build/wallpaper/sleep i wtyczka org.tide.cat), bez zmiany pulpitu
make install FORCE=1            # nadpisuje także własną tapetę
```

### Kot na pulpicie

W wariancie `cat` oba pulpity dostają wtyczkę tapety **„Tide: kot”** (`org.tide.cat`, w `~/.local/share/plasma/wallpapers`). Każdy monitor pokazuje swój kadr wspólnego płótna, tak jak przy zwykłych obrazach, i ten sam kadr co ekran blokady. Gdy przez **15 s** (`wallpaper.dozeAfterS`) nie było klawiatury ani myszy, kot zamyka oczy przenikaniem w 1100 ms (`lockscreen.sleepMs`); pierwszy ruch albo klawisz otwiera je w 650 ms (`lockscreen.wakeMs`). Przy wyłączonych animacjach zmiana jest natychmiastowa. W spoczynku tapeta nic nie robi — dwa nieruchome obrazy, bez zegarów.

Bezczynność liczy KWin (protokół Wayland `ext_idle_notifier_v1`, liczy się tylko wejście użytkownika, odtwarzany film nie trzyma kota obudzonego), a podaje ją `tide-daemon` przez D-Bus (obiekt `/Idle`, [opis](docs/tide-daemon.md#bezczynność)). Bez usługi kot po prostu czuwa. Próg czyta usługa z kopii palety, więc po jego zmianie trzeba `make install-daemon`.

```bash
make install-daemon install-wallpaper   # usługa (bezczynność) i wtyczka na obu pulpitach
make desktop-cat-preview                # zagnieżdżona sesja: build/desktop-cat/awake.png, asleep.png, waking.png, awake-again.png
qdbus6 org.tide.Daemon /Idle org.tide.Daemon.Idle.State   # {"available": true, "idle": false, "thresholdMs": 30000, …}
```

Zwykłe obrazy w `wallpapers/Tide/cat/` zostają: wskazuje je konfiguracja „Obraz” każdego pulpitu (wybór „Obraz” w ustawieniach pulpitu daje tego samego, czuwającego kota) i tapeta ekranu blokady, a kopia zapasowa sprzed wtyczki po `make restore` wraca do nich. Po ponownej instalacji z innym układem monitorów nowe kadry wtyczki pojawią się po `systemctl --user restart plasma-plasmashell` albo ponownym zalogowaniu.

**Własna tapeta.** Wystarczy ustawić inny obraz w Plasmie (prawy klik na pulpicie, Konfiguruj pulpit i tapetę). Kolejne `make install` nie ruszy pulpitu, na którym jest tapeta inna niż Tide albo ta sprzed Tide, chyba że podasz `FORCE=1`. `make uninstall` przywraca tapety sprzed Tide tylko tam, gdzie nadal jest tapeta Tide (obrazy albo wtyczka kota), i usuwa katalog `wallpapers/Tide` oraz wtyczkę `org.tide.cat`.

## Wygląd aktywności

Pulpit może mieć kilka aktywności KDE w jednej sesji. Każda ma wygląd z opisu w `theme/activities/`: **praca** (`work.json`, dzisiejszy Mist: `TideMist`, styl `Tide`, kot, ekran blokady Mist, obecne panele) i **Stream** (`stream.json`, paleta Streamer z PT-34, panele z PT-36). Wygląd `stream` dostaje aktywność przypisana do niego przy utworzeniu (`$XDG_CONFIG_HOME/tide/activities.json`, po ID aktywności); każda inna, także nowa, wygląda jak praca. Ikony, kursor, fonty, dekoracja okien, Konsole, Vivaldi i Claude są wspólne.

Po każdej zmianie aktywności (skrót, `Meta+Q`, widżet, KDE Connect) `tide-daemon` nakłada cały wygląd docelowej aktywności, a przy logowaniu porównuje pulpit z opisem i naprawia różnice ([opis](docs/tide-daemon.md#wygląd-aktywności), decyzja 78). Przełączanie wyglądu zależy od usługi: bez działającego `tide-daemon` (albo bez `make install-activity-look`) zmiana aktywności zostawia poprzedni schemat, styl i blokadę, a nowa aktywność dostaje swój wygląd dopiero przy następnym starcie usługi albo po `python3 scripts/activity_look.py apply`. Nakładane są tylko różnice, narzędziami Plasmy, więc otwarte okna przerysowują się bez restartu. Instalatory też: `make install` w aktywności Stream nie przestawi jej na Mist, a tapeta trafia tylko na pulpity bieżącej aktywności.

```bash
make activity-look-check     # czy pulpit zgadza się z opisem bieżącej aktywności — tylko odczyt
make activity-look-status    # przypisania, dostępne wyglądy, dziennik ostatniego przełączenia
make install-activity-look   # kopia silnika dla usługi w ~/.local/share/tide/activity-look (część make install)
```

### Aktywność Stream

`make install-stream-activity` tworzy aktywność „Stream” (ikona `tide-stream`: znak pada z marki Streamer w złocie
z palety) i przypisuje jej wygląd Stream; powtórzone nic nie dubluje, a bieżąca aktywność się nie zmienia. Panele,
konsola i filtr powiadomień nakładają się przy pierwszym wejściu (`Meta+Q`). `make uninstall-stream-activity` zdejmuje
przypisanie i usuwa aktywność, ale nie wtedy, gdy jest bieżąca. Oba cele są poza `make install` i `make uninstall`
(decyzja 80).

W Stream (panele Plasmy są wspólne dla aktywności, więc praca nic nie traci):

- **lewy monitor**: dock Stream na dole — Stream, Profil gracza, Observer (tylko w trybie Observer) · zadania · Kamera,
  „?” (miejsce na PC ⇄ Xbox z PT-38 przed „?”); na pulpicie jeden widżet „Lewy monitor Stream” (`org.tide.stream.desk`:
  konsola streamu albo Granie) na całą szerokość i wysokość nad dockiem, z marginesem 32 px (dokładany tylko wtedy,
  gdy go brakuje — własnego ułożenia nie ruszamy). Sceny i czat z wcześniejszej instalacji pierwsze wejście z deskiem
  zdejmuje z pulpitu Stream (tylko te dwa widżety i tylko tam); bez pakietu desku zostaje para Sceny (ok. 1/3
  szerokości) i czat;
- **DP-2**: dock bez kafelków pracy — zostaje System i zadania; kafelki pracy same się chowają
  (`widgets/lib/ActivityLook.qml` pyta tide-daemon), ich ustawienia i miejsca zostają;
- **pasek boczny pracy**: schowany i zwężony do minimum (bez kafelków); dotknięcie środka lewej krawędzi
  nie wysuwa paska, a przy górnym końcu pozostaje tylko mały cień panelu (ok. 64 × 16 px w podglądzie);
  `Meta+S` nic nie robi; po powrocie do pracy wraca dokładnie taki, jaki był;
- **powiadomienia**: wyskakują tylko KDE Connect, rdzeń Streamer (gdy wysyła `x-kde-appname=streamer`) i krytyczne;
  reszta, także Discord i Telegram, czeka w historii. Usługa trzyma „Nie przeszkadzać” na czas Stream i zwalnia je
  przy wyjściu; ustawienia powiadomień pracy wracają co do bajtu;
- **ekran blokady**: wariant Streamer bez linii „N agentów pracuje”.

Po powrocie do pracy panele są dokładnie jak przed wejściem (`panels.py dump` bez różnic — sprawdzane w zagnieżdżonej
sesji: `make stream-activity-preview`, wyniki w `build/stream-activity/`).

```bash
make install-stream-activity     # aktywność Stream z ikoną i przypisanym wyglądem
make uninstall-stream-activity   # usuwa ją (odmawia, gdy Stream jest bieżąca)
make stream-activity-status      # przypisane aktywności Stream — tylko odczyt
make stream-activity-preview     # zagnieżdżona sesja: praca → Stream → praca, zrzuty i porównanie paneli
```

## Ikony

Nakładka „Tide” dziedziczy po motywie bazowym (domyślnie McMojave-circle-dark, inny przez `make install ICON_BASE=...`) i dokłada tylko to, czego bazie brakuje: aliasy dla ikon Toolboksa JetBrains i błędnych mapowań (`code`), okrągłe kafelki z oryginalnych ikon aplikacji spoza motywu, glify w kolorach palety dla aplikacji bez ikony i foldery przefarbowane na teal. Aplikacje, które w `Icon=` podają ścieżkę do pliku (snapy, Zed, Docker), dostają lokalną kopię `.desktop` w `~/.local/share/applications` ze znacznikiem `X-Tide-Icons=override`. Ręczne poprawki leżą w `theme/icons/fixes.json`.

Aplikacje z docka i wszystkie zainstalowane programy i narzędzia mają w nakładce **stonowane glify**: zaokrąglony kwadrat w ciemnym odcieniu pastelu z palety i jednokolorowy znak aplikacji w tym pastelu (decyzje 31 i 39). Bez glifu zostają tylko gry ze Steama i narzędzia deweloperskie Plasmy z ogólnymi nazwami ikon (decyzja 40). Ta sama ikona jest w docku, w menu i w KRunnerze. Lista to `theme/icons/glyphs.json`: nazwy ikon aplikacji, źródło znaku i kolor (pastel albo token `text.*`), opcjonalnie `scale`. Znak pochodzi z ikony symbolicznej motywu bazowego (albo Breeze), z [Simple Icons](https://simpleicons.org) (CC0, wersja przypięta w pliku, każde logo ma zapisaną sumę SHA-256; pobierane przy budowaniu do `~/.cache/tide/simple-icons/`) albo z ręcznie narysowanego znaku w `theme/icons/glyphs/`. Nazwy z Toolboksa JetBrains (`jetbrains-rider-<uuid>`) trafiają do glifu przez wzorce z `fixes.json`. Gdy źródła nie ma albo suma się nie zgadza, aplikacja zostaje przy swojej ikonie, a `tide-icons` wypisuje ostrzeżenie. Nowe logo Simple Icons: dopisz aplikację do `glyphs.json`, uruchom `tide-icons pin-glyphs` (dopisze sumę), obejrzyj `make icons-preview` i zatwierdź.

```bash
tide-icons audit        # skąd ikony widocznych aplikacji, które nie mają glifu, czy przypięte w docku go mają;
                        # kod 1, gdy któraś nie pasuje albo przypięta nie ma glifu
tide-icons audit --json
tide-icons audit --theme Tide --icons-dir build/icons   # to samo dla nakładki z make icons-build, przed instalacją
tide-icons glyph tide-worktrees --out x.png             # glif z ręcznego znaku do PNG (bez motywu i sieci)
tide-icons sync         # po instalacji nowej aplikacji: odświeża nakładkę i kopie .desktop
tide-icons themes       # porównanie kandydatów na motyw bazowy
make icons-build        # sama nakładka do podglądu w build/icons/Tide
make icons-preview      # arkusz wszystkich glifów obok obecnych ikon: build/icons/glyphs-preview.png
tide-icons pin-glyphs   # sumy SHA-256 nowych logo Simple Icons do glyphs.json
```

`make install` instaluje polecenie do `~/.local/bin/tide-icons`. Odinstalowanie usuwa nakładkę, tylko oznaczone kopie `.desktop` (pliki użytkownika, które nadpisaliśmy, wracają z kopii) i dostęp Flatpaka do ikon, jeśli dał go Tide.

## Ekran logowania (SDDM)

Motyw SDDM „tide” w stylu Mist: tapeta Tide (w wariancie `cat` kot z otwartymi oczami; wycinek dla ekranu 2560×1440 i dla 1920×1080, jak na pulpicie, przyciemniony raz kolorem pulpitu o `opacity.loginDim`), logo „trzy fale”, zegar i data w Geist u góry pośrodku, awatar (zdjęcie albo inicjał), imię, pole hasła (karta z palety, teal przy fokusie), „Zmień użytkownika”, wybór sesji, układ klawiatury („PL”), przyciski uśpienia, ponownego uruchomienia i wyłączenia oraz komunikat o złym haśle w kolorze róż. SDDM otwiera osobne okno na każdym monitorze i każde ma pełny formularz, jak w Breeze.

**Wybór użytkownika.** „Zmień użytkownika” (albo Escape w pustym polu hasła) pokazuje karty kont z prawdziwego `userModel` SDDM (zdjęcie albo inicjał, imię, nazwa konta) i kartę „Inny użytkownik” (wpisanie nazwy konta, jak „Inny…” w Breeze). Zaznaczone jest konto, które było wybrane (na starcie ostatnio zalogowane, `userModel.lastIndex`). Strzałki, Tab/Shift+Tab, Home i End przesuwają zaznaczenie, Enter i spacja wybierają, Escape wraca do formularza; strzałki w pustym polu hasła nadal przełączają konto bez otwierania listy. Tło stoi w miejscu — zmienia się tylko środek ekranu.

Motyw używa tylko QtQuick i obiektów, które SDDM daje każdemu motywowi (`sddm`, `userModel`, `sessionModel`, `keyboard`, `config`), bez modułów `org.kde.*`. Czcionki Geist, tapety, logo i ikony są w katalogu motywu, bo ekran logowania działa jako użytkownik `sddm` i nie widzi katalogu domowego. Kolory są w `Tokens.qml`, generowanym z palety.

```bash
make sddm-preview      # zrzuty w build/sddm/preview: atrapa greetera (formularz, wybór użytkownika z dwoma kontami,
                       # „Inny użytkownik”, błąd; 2560×1440 i 1920×1080) i prawdziwy greeter w wirtualnym KWinie
make sddm-check        # samo sprawdzenie, bez sudo
make install-sddm      # sprawdzenie, potem jedno sudo: kopia do /usr/share/sddm/themes/tide i /etc/sddm.conf.d/zz-tide.conf
make uninstall-sddm    # sudo: usuwa oba, wraca motyw sprzed Tide
make sddm-status       # który motyw wybierze SDDM i skąd
```

`make install-sddm` nie jest częścią `make install`, bo wymaga sudo. Przed zmianą czegokolwiek w systemie motyw jest sprawdzany dwa razy (jako użytkownik i jeszcze raz na kopii w `/usr/share/sddm/themes/.tide-new`): komplet plików, atrapa greetera w `qml6` poza ekranem (dwa konta, wstępny wybór, wybór konta prawdziwymi klawiszami, „Inny użytkownik”, puste hasło, logowanie, komunikat błędu, czcionki, każde ostrzeżenie QML to błąd) i `sddm-greeter-qt6 --test-mode` przez kilka sekund. Gdy sprawdzenie nie przejdzie, w systemie nic się nie zmienia.

**Poprzedni motyw.** Tide nie zmienia `kde_settings.conf` (tam motyw zapisują Ustawienia systemowe), tylko dokłada `zz-tide.conf`, czytany po nim. Motyw sprzed Tide jest zapisany w nagłówku tego pliku i w `/var/lib/tide/sddm-previous.json`; po odinstalowaniu wraca sam. Dopóki Tide jest zainstalowany, wybór motywu w Ustawieniach systemowych nie zadziała — najpierw `make uninstall-sddm`.

**Gdy coś pójdzie nie tak.** Błąd w QML: SDDM sam przechodzi na wbudowany motyw („Fallback to embedded theme”), a brak katalogu motywu daje to samo. Gdyby ekran logowania mimo to nie działał, z konsoli tekstowej (Ctrl+Alt+F3, zalogowanie):

```bash
sudo rm /etc/sddm.conf.d/zz-tide.conf && sudo systemctl restart sddm
```

## Ekran startowy (Plymouth)

Motyw Plymouth „tide” to pierwszy ekran po GRUB-ie: grafitowe tło (`background.desktop`), pośrodku logo „trzy fale” na kafelku i 44 px niżej cienki pasek postępu — w tym samym miejscu i rozmiarze co ekran ładowania Plasmy. Wejście to przenikanie `splash.fadeMs` (kafelek, potem fale co `plymouth.waveDelayMs`), potem fale po kolei „oddychają” (krycie 1 → `plymouth.breatheTo` → 1, cykl `splash.pulseMs`, ta sama krzywa co logo w `Splash.qml`). Przy wyłączaniu i restarcie jest samo logo, przy aktualizacjach systemu pasek i „Instalowanie aktualizacji · 42%”. Zaszyfrowany dysk: zamiast paska podpowiedź z systemu i pole hasła jak na ekranie blokady (karta, ramka i obwódka w tealu, kropki w kolorze tekstu, „Hasło” gdy puste), pod nim „Caps Lock jest włączony” w maśle; komunikaty systemu (np. sprawdzanie dysków) niżej w kolorze wyciszonym.

```bash
make plymouth-preview        # klatki bez roota w build/plymouth/preview (boot-*.png, password-*.png, boot.gif, …)
make plymouth-check          # samo sprawdzenie, bez sudo
make install-plymouth        # sprawdzenie, potem jedno sudo: kopia, update-alternatives, update-initramfs -u
make uninstall-plymouth      # sudo: wraca motyw sprzed Tide, update-initramfs -u
make plymouth-status         # który motyw wybierze Plymouth i czy jądro ma „quiet splash”
make plymouth-real-preview   # składa motyw dla /run i wypisuje polecenie podglądu na prawdziwym plymouthd
```

**Moduł script.** `two-step.so` nie ma w systemie (pakiet `plymouth-themes`), a `script.so` jest — tego samego używa Breeze. Skrypt to tokeny z palety (kolory, czasy, wymiary w każdej skali; `theme/plymouth/build.py`) i stała logika (`theme/plymouth/logic.script`). Obrazy to warstwy logo (kafelek i każda fala osobno, żeby fale mogły oddychać po kolei), tor i wypełnienie paska oraz pole hasła — w czterech skalach: 0,75, 1, 4/3 i 2 względem 1920×1080. Plymouth rysuje te same duszki na wszystkich ekranach, ustawiając mniejsze środkiem do środka największego, więc skala idzie za najmniejszym ekranem: przy obu monitorach logo ma 128 px jak w Plasmie, sam monitor 2560×1440 dostaje 171 px, 4K bez skalowania 256 px. Plymouth podaje skryptowi wymiary logiczne, więc przy skalowaniu HiDPI (DeviceScale) obrazy są powiększane. Tekst rysuje `label-pango.so` czcionką z initramfs: hook Plymouth kopiuje tylko Ubuntu (jako „Sans”), więc Geist z palety jest pierwszy na liście, a faktycznie pisze Ubuntu.

**Podgląd bez roota.** Prawdziwy `plymouthd` potrzebuje roota i ekranu (renderery drm i frame-buffer; renderera x11 w KDE neon nie ma). `theme/plymouth/harness.py` to atrapa modułu script: wykonuje ten sam `tide.script` na tych samych obrazach (interpreter języka skryptów Plymouth, biblioteki Math, Window, Sprite, Image, Plymouth i model ekranów jak w `script-lib-sprite.c`) i rysuje klatki w Pillow. Atrapa jest surowsza niż Plymouth: błąd składni, wywołanie czegoś, co nie jest funkcją, brakujący obraz czy działanie na NULL to wyjątek z numerem wiersza. Na niej działają `make plymouth-preview`, `make plymouth-check` i testy.

**Podgląd na prawdziwym plymouthd** (opcjonalnie, uruchamia użytkownik): `make plymouth-real-preview`, potem na konsoli tekstowej (Ctrl+Alt+F3) `sudo sh theme/plymouth/preview_real.sh build/plymouth/run/tide`. Motyw trafia tylko do `/run/plymouth/themes/tide` (tmpfs), wybiera go podrobiony wiersz poleceń jądra (`--kernel-command-line … plymouth.splash=tide`), a alternatywy, initramfs i GRUB zostają bez zmian. Pokaz: start, komunikat, pole hasła (wpisz cokolwiek), tryb aktualizacji; dziennik w `/run/tide-plymouth-debug.log` (błędy skryptu to „Parser error” i „Execution error”). Powrót do sesji: Ctrl+Alt+F1 albo F2.

**Instalacja.** `make install-plymouth` nie jest częścią `make install`, bo wymaga sudo i odświeża initramfs. KDE neon, jak Ubuntu, nie ma `plymouth-set-default-theme`: domyślny motyw to alternatywa `default.plymouth`, z której czyta też hook initramfs. `scripts/plymouth_system.py`:

1. przerywa, zanim cokolwiek zmieni, jeśli inny motyw wymusza `plymouth.splash=` w wierszu poleceń jądra albo `Theme=` w `/etc/plymouth/plymouthd.conf` lub `/usr/share/plymouth/plymouthd.defaults`,
2. kopiuje motyw do `/usr/share/plymouth/themes/.tide-new` i sprawdza tę kopię (`check.py`: pliki, ścieżki, skrypt w atrapie na siedmiu zestawach ekranów i we wszystkich trybach) — gdy nie przejdzie, w systemie nic się nie zmienia,
3. przy pierwszej instalacji zapisuje w `/var/lib/tide/plymouth-previous.json` motyw sprzed Tide (wartość i tryb alternatywy: auto albo ręczny) i jego plik `.grub`,
4. podmienia `/usr/share/plymouth/themes/tide`, rejestruje alternatywę (priorytet 100, niższy niż Breeze) i wybiera ją ręcznie (`update-alternatives --set`); podrzędne `default.grub` wskazuje `tide.grub` — kopię `.grub` poprzedniego motywu, więc `update-grub` da to samo menu co dotąd,
5. uruchamia `update-initramfs -u` (najnowsze jądro).

`make uninstall-plymouth` przywraca zapamiętany wybór (`--auto` albo `--set` poprzedniego motywu), wyrejestrowuje „tide”, usuwa katalog i zapis stanu, potem znów `update-initramfs -u`. Nowy ekran widać dopiero przy następnym uruchomieniu.

**Wiersz poleceń jądra.** Plymouth pokazuje grafikę tylko z `splash` w wierszu poleceń jądra. Na tym komputerze jest (`/proc/cmdline`: `… ro quiet splash resume=… nvidia.NVreg_EnableGpuFirmware=0 vt.handoff=7`, `GRUB_CMDLINE_LINUX_DEFAULT='quiet splash …'` w `/etc/default/grub`), więc nic nie trzeba zmieniać. Tide nigdy nie zmienia GRUB-a; `make plymouth-status` pokazuje stan i podpowiada, co dopisać, gdyby `splash` zniknął. Karta NVIDIA (sterownik `nvidia-drm` z `modeset=1`): jeśli moduły NVIDIA nie są w initramfs, Plymouth zaczyna na prostym framebufferze (zwykle jeden ekran w rozdzielczości z firmware) i przechodzi na pełne ekrany, gdy sterownik się wczyta — skrypt przelicza wtedy układ (`SetDisplayHotplugFunction`).

**Gdy coś pójdzie nie tak.** Błąd w skrypcie daje pusty grafitowy ekran zamiast logo, ale start systemu i pytanie o hasło działają dalej (hasło można wpisać na ślepo, Esc przełącza na tekst). Powrót do Breeze z konsoli tekstowej:

```bash
sudo update-alternatives --auto default.plymouth && sudo update-initramfs -u
```
