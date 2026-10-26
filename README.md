# Tide

Własny motyw KDE Plasma 6 z widżetami do pracy z agentami AI. Pierwszy wariant to **Mist**: miękki ciemny grafit, teal i lawenda, pastele do statusów.

- Plan wdrożenia: https://claude.ai/artifact/FbiGQYsWQ3gWQiDHLxvKFv
- Mocki: https://claude.ai/artifact/1h17Au71mmdEX5sNBSaAgC
- Decyzje i ich uzasadnienia: [docs/decisions.md](docs/decisions.md)

## Środowisko

KDE neon, Plasma 6.8, Wayland, dwa monitory (2560×1440 główny i 1920×1080). Motyw instaluje się do katalogu domowego. Sudo wymaga tylko ekran logowania (SDDM).

## Struktura

| Katalog | Zawartość |
| --- | --- |
| `theme/palette/` | Paleta wariantu w JSON, jedyne źródło kolorów, promieni, przezroczystości i rozmiarów pasków dla generatorów |
| `theme/wallpaper/` | Generator tapety z palety (jedno płótno na oba monitory) |
| `theme/plasma/` | Styl Plasmy „Tide” (desktoptheme): generator ramek, podgląd (`preview.py`) i sprawdzenie prawdziwym KSvg (`ksvg_check.py`) |
| `theme/lookandfeel/` | Motyw globalny „Tide” (`org.tide.desktop`): wskazania na składniki Tide, podglądy i ekran ładowania (`splash/`, zrzuty z prawdziwego ksplashqml w `splash_shot.sh`) |
| `theme/lockscreen/` | Ekran blokady Tide jako pakiet powłoki Plasmy (QML na bazie ekranu Plasmy 6.8), zrzuty z prawdziwego greetera, sprawdzenie kopii powłoki w zagnieżdżonej sesji (`shell_copy_check.sh`) |
| `theme/icons/` | Nakładka ikon „Tide” i polecenie `tide-icons` (audyt, synchronizacja, podgląd) |
| `theme/sddm/` | Ekran logowania SDDM „tide”: QML, składanie z palety (`build.py`), sprawdzenie przed instalacją (`check.py`, atrapa greetera w `harness/`), zrzut prawdziwego greetera (`greeter_shot.sh`) |
| `scripts/` | Kopie zapasowe i przywracanie wyglądu (`backup.sh`, `restore.sh`, `paths.sh`), instalatory komponentów (`sddm_system.py` to jedyny uruchamiany przez sudo) i synchronizacja kopii powłoki z ekranem blokady (`shell_sync.py`) |
| `scripts/panels/` | Skrypty plasmashell górnych pasków (zrzut, instalacja, kolejność, odinstalowanie) |
| `tools/` | Narzędzia do ikon: `make_tile.py` (kafelek z oryginalnej ikony), `make_tint.py` (eksperyment z przefarbowaniem, odrzucony) |
| `daemon/` | Usługa `tide-daemon`: sesje agentów i worktrees przez D-Bus ([opis i kontrakt](docs/tide-daemon.md)) |
| `widgets/lib/` | Wspólna biblioteka QML widżetów: karta, miernik, pierścień, wykres słupkowy z historią próbek, kropka i licznik stanu, źródło danych z usługi |
| `widgets/agents/` | Widżet agentów `org.tide.agents`: liczby sesji w panelu, lista sesji w popupie |
| `widgets/limits/` | Widżet `org.tide.limits`: pozostałe limity ChatGPT i Claude z usługi QuotaApp |
| `widgets/worktrees/` | Kafelek worktrees `org.tide.worktrees`: ile do sprzątnięcia (i ile GB), ile w pracy; kliknięcie otwiera okno |
| `apps/worktrees/` | Okno „Tide Worktrees” (QML + PyQt6, plik `.desktop`): grupy W pracy / Do sprzątnięcia / Do decyzji, szukanie, filtry repozytoriów, usuwanie zaznaczonych |
| `widgets/system/` | Widżet `org.tide.system`: procesor, pamięć, karta graficzna i dyski z czujników ksystemstats |
| `kwin/` | Skrypt KWin `tide-agent-jump` ze skrótem Meta+A i jego instalator |
| `krunner/` | Plik wtyczki KRunnera „Tide: agenci i projekty” i jej instalator (wyniki daje tide-daemon) |
| `mk/` | Cele `make` poszczególnych komponentów (`install-*`, `uninstall-*`, katalogi testów) |
| `docs/` | Decyzje projektowe, opis usługi |

Ekran ładowania jest częścią motywu globalnego (`theme/lookandfeel/splash/`).

## Agenci AI

```bash
make install-daemon install-widget-agents   # usługa i widżet (bez kopii zapasowej wyglądu)
make install-agent-jump                     # skrót Meta+A: przejście do agenta, który czeka najdłużej
make install                                # wszystko, najpierw kopia zapasowa
make test                                   # testy wszystkich komponentów
```

Gdy agent zaczyna czekać, przychodzi powiadomienie „Agent czeka: <projekt>” z przyciskiem „Przejdź” (raz na każde wejście w czekanie). Meta+A przenosi do okna sesji, która czeka najdłużej; gdy nikt nie czeka, nic się nie dzieje. Jeśli Meta+A jest już zajęty (instalator to wypisze), trzeba go zwolnić w Ustawieniach systemowych → Skróty i przypisać akcji „Tide: przejdź do agenta, który czeka najdłużej”.

**KRunner.** `make install-daemon install-krunner` dodaje wtyczkę „Tide: agenci i projekty”: po wpisaniu w KRunnerze (Alt+Spacja) nazwy projektu, gałęzi albo fragmentu tytułu rozmowy pokazuje sesje Claude Code i Codexa z ostatnich 30 dni (`Claude · plasma-tide · <tytuł>`, `<gałąź> · 3 dni temu`), projekty z `~/DevWorkspace` i worktrees. Enter wznawia sesję w nowym oknie Konsoli w jej katalogu (albo przenosi do okna, jeśli sesja jeszcze działa), otwiera projekt lub worktree w Konsoli; akcja obok otwiera w Riderze. `ai <słowa>` szuka tylko sesji. Szczegóły: [docs/tide-daemon.md](docs/tide-daemon.md#krunner).

Widżet „Tide · Agenci” dodaje się do panelu z listy widżetów. Po aktualizacji już dodanego widżetu Plasma wczyta nowy kod dopiero po `systemctl --user restart plasma-plasmashell`. Kolory biblioteki QML są generowane z palety przy składaniu pakietu (`widgets/stage.py` → `Tokens.qml`).

## Limity AI

Widżet „Tide · Limity AI” (`org.tide.limits`) pokazuje w kafelku pozostały procent ChatGPT i Claude (niższy z limitów sesji i tygodniowego), a w popupie oba limity z resetami. Dane bierze wprost z lokalnego API usługi [QuotaApp](https://github.com/julia-lakomy/QuotaApp) (`limity-ai-widget.service`), bez tide-daemon — usługa musi być zainstalowana i działać.

```bash
make install-widget-limits     # instalacja albo aktualizacja widżetu
make swap-limits-widget        # podmiana starego widżetu QuotaApp w dolnym panelu (kopia zapasowa przed)
make uninstall-widget-limits   # usuwa tylko org.tide.limits
```

**Token.** Port i token API są w `~/.config/limity-ai-widget/config.json` (tworzy go instalator QuotaApp, tryb 0600). Przy instalacji `widgets/stage.py` przepisuje je do `contents/code/Config.js` w pakiecie, z trybem 0600; `widgets/install.sh` usuwa ten plik ze złożonej kopii w `build/` zaraz po instalacji. Token nigdy nie trafia do repozytorium. Po zmianie tokenu wystarczy ponowne `make install-widget-limits`.

**Podmiana w panelu.** `make swap-limits-widget` robi kopię zapasową i przez `evaluateScript` Plasmy usuwa `org.limityai.widget` z dolnego panelu, a w to samo miejsce wstawia `org.tide.limits` (`TIDE_PANEL_SCREEN=n` ogranicza do ekranu n). Skrypt bez wykonywania: `bash scripts/swap-limits-widget.sh --print`. Panel z zablokowanymi widżetami trzeba najpierw odblokować.

## System

Widżet „Tide · System” (`org.tide.system`) pokazuje w kafelku trzy pierścienie: procesor (teal), pamięć (lawenda) i pełniejszą z partycji / i /home (brzoskwinia). W popupie — a później w pasku bocznym — jest wykres procesora z ostatniej minuty (próbka co 2 s), pamięć z trzema programami zajmującymi najwięcej, karta graficzna z VRAM i obie partycje z wolnym miejscem.

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

## Styl Plasmy i górne paski

Styl „Tide” zawiera tylko ramki o nowym kształcie: tło panelu (pasek z prefiksem `north` i promieniem paska, dock i panele boczne z promieniem docka), wyskakujące okna z nagłówkiem i stopką oraz podpowiedzi — w wersjach zwykłej, `translucent/`, `solid/` i `opaque/`, jak w Breeze. Resztę Plasma bierze z Breeze, bo „default” jest zawsze ostatnim motywem zapasowym (dodatkowo `FallbackTheme=default` w `plasmarc`). Kolory nadal pochodzą ze schematu: styl nie ma pliku `colors`, a wypełnienia mają klasy `ColorScheme-*`. Licencja i pochodzenie: `NOTICE` w katalogu stylu (LGPL-2.0-or-later, jak Breeze).

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

## Worktrees

```bash
make install-daemon install-widget-worktrees install-worktrees-window
make preview-worktrees-window   # zrzut okna na danych z tego komputera (tylko odczyt): build/worktrees-window.png
```

Kafelek „Tide · Worktrees” w docku pokazuje, ile worktrees można sprzątnąć bez ryzyka i ile to GB (mięta), a pod spodem ile jest w pracy (teal). Kliknięcie otwiera okno **Tide Worktrees** (własna ikona i nazwa w pasku zadań, także w menu aplikacji). Okno dzieli worktrees na trzy grupy:

- **W pracy** (teal) — pracuje w nim agent albo coś zmieniło się w ostatniej dobie; tylko otwieranie w Konsoli albo Riderze,
- **Do sprzątnięcia** (mięta) — scalone z **lokalną** gałęzią główną (master, main albo develop, bez łączenia z serwerem), bez zmian i bez agenta. Każdy ma pole wyboru; „Usuń zaznaczone” najpierw wymienia dokładnie katalogi i gałęzie, które znikną. Usuwanie bez `--force`, gałąź tylko przez `git branch -d`, a usługa sprawdza każdy worktree jeszcze raz tuż przed usunięciem,
- **Do decyzji** (masło) — stoją od ponad doby, ale mają niescalone commity albo zmiany; widać dlaczego („4 commity · 3 zmiany”), można je tylko otworzyć. Starsze niż 30 dni są wyciszone.

Wiersz mówi, co to za worktree: narzędzie (Codex, Claude), tytuł z nazwy gałęzi bez prefiksu (`codex/summary-tabs-03-version-axis` → „summary-tabs 03 · version axis”), temat ostatniego commita albo tytuł powiązanej sesji agenta, ostatnią aktywność i rozmiar. Worktrees na gałęzi głównej są w zwiniętej sekcji „Gałęzie główne” i nigdy nie są sprzątane, a wpisy po ręcznie usuniętych katalogach porządkuje przycisk `git worktree prune` przy repozytorium. Katalog projektów to `~/DevWorkspace` (zmiana: `TIDE_WORKSPACE` w środowisku usługi albo `--workspace`).

## Motyw globalny i ekran blokady

Motyw globalny „Tide” (`org.tide.desktop`) spina w Ustawieniach systemowych to, co instalują pozostałe części: schemat TideMist, styl Plasmy Tide, dekorację Aurorae Tide (przyciski po prawej: minimalizuj, maksymalizuj, zamknij), ikony Tide, kursor Nordic-cursors i czcionki Geist. Nie ma w nim skryptów układu ani tapety — panele i tapety ustawiają `scripts/panels.py` i `scripts/wallpaper.py`.

`make install` (cel `install-lookandfeel`) kopiuje motyw do `~/.local/share/plasma/look-and-feel/org.tide.desktop` i zapisuje tylko wybór motywu (`kdeglobals [KDE] LookAndFeelPackage`, wyłączona automatyczna zmiana motywu) oraz tapetę ekranu blokady (obraz Tide głównego monitora w `kscreenlockerrc`). **Nie wywołuje `plasma-apply-lookandfeel`** i nie dotyka paneli. Poprzednie wartości są w `~/.local/state/tide/lookandfeel-previous.json`, `make uninstall` je przywraca.

W Ustawieniach systemowych → Motyw globalny można kliknąć „Tide”, ale **nie zaznaczać „Układ pulpitu i okien”**: Plasma kasuje wtedy całą konfigurację paneli (`plasma-org.kde.plasma.desktop-appletsrc`) i ładuje układ Breeze. Ratunek: `make restore NAME=<kopia>` i ponowne `make install`.

**Ekran blokady.** W Plasmie 6.8 greeter (`kscreenlocker_greet`) bierze ekran blokady wyłącznie z pakietu powłoki (`plasma/shells/<powłoka>/contents/lockscreen`), a powłokę wybiera tak samo jak plasmashell (`plasmashellrc [Shell] ShellPackage`, domyślnie `org.kde.plasma.desktop`). Katalog `lockscreen/` w motywie globalnym jest ignorowany, a klucz `kscreenlockerrc [Greeter] Theme` nie istnieje. Dlatego ekran Tide powstaje jako pakiet powłoki `org.tide.desktop` z samym `lockscreen/` (do trybu testowego), a prawdziwym ekranem blokady staje się przez **kopię powłoki** (decyzja 27, wariant a): `make install` kopiuje cały systemowy `/usr/share/plasma/shells/org.kde.plasma.desktop` do `~/.local/share/plasma/shells/org.kde.plasma.desktop` i podmienia w nim tylko `contents/lockscreen` na ekran Tide. Pakiet w katalogu domowym wygrywa z systemowym, więc greeter bierze z niego ekran Tide, a plasmashell resztę — identyczną z systemem. Powłoka się nie zmienia (`ShellPackage` zostaje `org.kde.plasma.desktop`), więc panele i plik `plasma-org.kde.plasma.desktop-appletsrc` też nie.

```bash
make lockscreen-screenshot   # zrzut prawdziwego greetera w zagnieżdżonym KWin, bez ekranu → build/lockscreen.png
make lockscreen-test         # greeter w trybie testowym jako okno (XWayland); hasło zamyka okno
make lockscreen-shell-check  # kopia powłoki w zagnieżdżonej sesji: plasmashell i greeter → build/lockscreen-shell-check
make shell-sync-status       # czy kopia powłoki jest włączona i jak skończyła się ostatnia synchronizacja
make lookandfeel-build       # sam motyw globalny w build/lookandfeel
```

**Synchronizacja kopii.** Po aktualizacji Plasmy plasmashell czytałby starą kopię, dlatego jednostka użytkownika `tide-shell-sync.service` (oneshot) uruchamia przy każdym logowaniu `~/.local/share/tide/shell-sync.py` — kopię skryptu `scripts/shell_sync.py`. Jednostka jest `WantedBy=plasma-workspace.target` i `Before=plasma-plasmashell.service`: startplasma uruchamia `plasma-workspace-wayland.target`, które wymaga `plasma-workspace.target`, a ono `plasma-core.target` z plasmashell, więc to jedna transakcja systemd i plasmashell czeka na koniec synchronizacji (sam plasmashell nie jest uporządkowany po `graphical-session-pre.target`, stąd jawne `Before=`). Skrypt liczy odcisk SHA-256 plików pakietu systemowego i ekranu Tide; gdy zgadza się z odciskiem zapisanym w kopii (`tide-shell-copy.json`), kończy się po ok. 40 ms. Gdy nie, składa nową kopię poza `plasma/shells/`, sprawdza ją prawdziwym greeterem (`kscreenlocker_greet --testing --shell`, poza ekranem, bez magistrali sesji, ok. 2 s) i dopiero wtedy podmienia. Gdy ekran Tide się nie wczyta, **usuwa kopię w całości** i zostaje ekran Plasmy z systemu; kolejna próba dopiero po następnej zmianie (aktualizacja Plasmy albo `make install`). Stan ostatniej decyzji: `~/.local/state/tide/shell-sync.json`, log: `journalctl --user -u tide-shell-sync`. `make uninstall` wyłącza jednostkę i usuwa ją razem z kopią, skryptem i stanem.

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

Wygląd z makiety: przyciemniona tapeta Mist (rozmyta przy widocznym polu hasła), zegar Geist 96 px i data („poniedziałek, 26 października”) w górnej części, logo „trzy fale”, nazwa użytkownika i pole hasła na karcie z tealową obwódką, na dole klawiatura, uśpienie i zmiana użytkownika jako same ikony. Linia „2 agenci pracują · 1 czeka” pochodzi z tide-daemon przez D-Bus sesji (greeter działa jako użytkownik i dziedziczy magistralę po KWin); pokazuje tylko liczby, bez nazw projektów, i znika, gdy usługi nie ma albo nikt nie pracuje. Gdy QML ekranu Tide się nie wczyta, greeter sam pokazuje awaryjny ekran KDE (sprawdzane w testach). Licencja i pochodzenie plików: `NOTICE` w pakiecie (GPL-2.0-or-later, jak ekran Plasmy).

## Ekran ładowania

Po zalogowaniu `ksplashqml` pokazuje ekran Tide z motywu globalnego (`contents/splash`): tapeta Mist przyciemniona kolorem pulpitu (przyciemnienie liczone przy składaniu pakietu, żeby gradient nie robił pasm), pośrodku pełne logo „trzy fale”, pod nim cienki tealowy pasek postępu (wymiary z sekcji `splash` palety, promień `radius.small` ograniczony do połowy wysokości paska), logo delikatnie „oddycha”, a całość wchodzi przenikaniem. QML to przerobiony ekran Breeze z tym samym API etapów: ksplashqml ustawia `stage` na 1…6 i kończy pracę po szóstym, więc pasek pokazuje `stage / 6`. Gdy w Ustawieniach wyłączono animacje, nic nie pulsuje. Ekran nie wydłuża startu: ksplashqml znika, gdy sesja jest gotowa, a obrazy wczytują się asynchronicznie.

`make install` zapisuje `ksplashrc [KSplash] Theme=org.tide.desktop` (poprzednia wartość w `lookandfeel-previous.json`), `make uninstall` przywraca poprzedni ekran, jeśli nadal wybrany jest Tide. Ekran działa też w podglądzie Ustawień systemowych → Ekran powitalny.

```bash
make splash-preview   # prawdziwy ksplashqml --test w zagnieżdżonym KWinie → build/splash/splash-*.png
```

Licencja: `NOTICE` w motywie (GPL-2.0-or-later, jak ekran Breeze, z którego pochodzi `Splash.qml`).

## Kopie zapasowe

Przed każdą zmianą wyglądu powstaje kopia w `~/.local/share/plasma-tide/backups`.

```bash
make backup NAME=przed-zmiana   # nowa kopia
make backups                    # lista kopii
make restore NAME=przed-tide    # powrót do wyglądu sprzed projektu
```

`restore` sam robi najpierw kopię bieżącego stanu, więc przywracanie też da się cofnąć. Po przywróceniu warto się wylogować i zalogować ponownie.

## Tapeta

Tapeta Mist powstaje z palety (sekcja `wallpaper` w `theme/palette/mist.json`: kolory plam, ich położenie, krycie, ziarno i stałe ziarno losowania). Generator rysuje jedno płótno obejmujące wszystkie monitory w ich prawdziwym położeniu i tnie je na ekrany, więc plamy przechodzą płynnie z lewego monitora na prawy. Delikatne ziarno z ditheringiem zapobiega pasmom na gradientach.

`make install` czyta układ monitorów z `kscreen-doctor -j` (gdy nie odpowiada, bierze znany układ: HDMI-A-1 1920×1080 po lewej, DP-2 2560×1440 po prawej), zapisuje po jednym obrazie na monitor w `~/.local/share/wallpapers/Tide/<złącze>.png` i ustawia je na pulpitach. Po zmianie monitorów wystarczy ponowne `make install`. Potrzebne są numpy i Pillow, które w KDE neon ma systemowy `/usr/bin/python3`.

```bash
make wallpaper-preview          # same obrazy w build/wallpaper, bez zmiany pulpitu
make install FORCE=1            # nadpisuje także własną tapetę
```

**Własna tapeta.** Wystarczy ustawić inny obraz w Plasmie (prawy klik na pulpicie, Konfiguruj pulpit i tapetę). Kolejne `make install` nie ruszy pulpitu, na którym jest tapeta inna niż Tide albo ta sprzed Tide, chyba że podasz `FORCE=1`. `make uninstall` przywraca tapety sprzed Tide tylko tam, gdzie nadal jest tapeta Tide, i usuwa katalog `wallpapers/Tide`.

## Ikony

Nakładka „Tide” dziedziczy po motywie bazowym (domyślnie McMojave-circle-dark, inny przez `make install ICON_BASE=...`) i dokłada tylko to, czego bazie brakuje: aliasy dla ikon Toolboksa JetBrains i błędnych mapowań (`code`), okrągłe kafelki z oryginalnych ikon aplikacji spoza motywu, glify w kolorach palety dla aplikacji bez ikony i foldery przefarbowane na teal. Aplikacje, które w `Icon=` podają ścieżkę do pliku (snapy, Zed, Docker), dostają lokalną kopię `.desktop` w `~/.local/share/applications` ze znacznikiem `X-Tide-Icons=override`. Ręczne poprawki leżą w `theme/icons/fixes.json`.

```bash
tide-icons audit        # skąd ikony widocznych aplikacji; kod 1, gdy któraś nie pasuje
tide-icons audit --json
tide-icons sync         # po instalacji nowej aplikacji: odświeża nakładkę i kopie .desktop
tide-icons themes       # porównanie kandydatów na motyw bazowy
make icons-build        # sama nakładka do podglądu w build/icons/Tide
```

`make install` instaluje polecenie do `~/.local/bin/tide-icons`. Odinstalowanie usuwa nakładkę, tylko oznaczone kopie `.desktop` (pliki użytkownika, które nadpisaliśmy, wracają z kopii) i dostęp Flatpaka do ikon, jeśli dał go Tide.

## Ekran logowania (SDDM)

Motyw SDDM „tide” w stylu Mist: tapeta Tide (wycinek dla ekranu 2560×1440 i dla 1920×1080, jak na pulpicie, lekko przyciemniony kolorem pulpitu), logo „trzy fale”, zegar i data w Geist, awatar albo inicjał, imię, pole hasła (karta z palety, teal przy fokusie), wybór sesji, układ klawiatury („PL”), przyciski uśpienia, ponownego uruchomienia i wyłączenia oraz komunikat o złym haśle w kolorze róż. SDDM otwiera osobne okno na każdym monitorze i każde ma pełny formularz, jak w Breeze.

Motyw używa tylko QtQuick i obiektów, które SDDM daje każdemu motywowi (`sddm`, `userModel`, `sessionModel`, `keyboard`, `config`), bez modułów `org.kde.*`. Czcionki Geist, tapety, logo i ikony są w katalogu motywu, bo ekran logowania działa jako użytkownik `sddm` i nie widzi katalogu domowego. Kolory są w `Tokens.qml`, generowanym z palety.

```bash
make sddm-preview      # zrzuty w build/sddm/preview: atrapa greetera i prawdziwy greeter w wirtualnym KWinie
make sddm-check        # samo sprawdzenie, bez sudo
make install-sddm      # sprawdzenie, potem jedno sudo: kopia do /usr/share/sddm/themes/tide i /etc/sddm.conf.d/zz-tide.conf
make uninstall-sddm    # sudo: usuwa oba, wraca motyw sprzed Tide
make sddm-status       # który motyw wybierze SDDM i skąd
```

`make install-sddm` nie jest częścią `make install`, bo wymaga sudo. Przed zmianą czegokolwiek w systemie motyw jest sprawdzany dwa razy (jako użytkownik i jeszcze raz na kopii w `/usr/share/sddm/themes/.tide-new`): komplet plików, atrapa greetera w `qml6` poza ekranem (logowanie, komunikat błędu, czcionki, każde ostrzeżenie QML to błąd) i `sddm-greeter-qt6 --test-mode` przez kilka sekund. Gdy sprawdzenie nie przejdzie, w systemie nic się nie zmienia.

**Poprzedni motyw.** Tide nie zmienia `kde_settings.conf` (tam motyw zapisują Ustawienia systemowe), tylko dokłada `zz-tide.conf`, czytany po nim. Motyw sprzed Tide jest zapisany w nagłówku tego pliku i w `/var/lib/tide/sddm-previous.json`; po odinstalowaniu wraca sam. Dopóki Tide jest zainstalowany, wybór motywu w Ustawieniach systemowych nie zadziała — najpierw `make uninstall-sddm`.

**Gdy coś pójdzie nie tak.** Błąd w QML: SDDM sam przechodzi na wbudowany motyw („Fallback to embedded theme”), a brak katalogu motywu daje to samo. Gdyby ekran logowania mimo to nie działał, z konsoli tekstowej (Ctrl+Alt+F3, zalogowanie):

```bash
sudo rm /etc/sddm.conf.d/zz-tide.conf && sudo systemctl restart sddm
```
