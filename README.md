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
| `theme/icons/` | Nakładka ikon „Tide” i polecenie `tide-icons` (audyt, synchronizacja, podgląd) |
| `theme/sddm/` | Ekran logowania SDDM „tide”: QML, składanie z palety (`build.py`), sprawdzenie przed instalacją (`check.py`, atrapa greetera w `harness/`), zrzut prawdziwego greetera (`greeter_shot.sh`) |
| `scripts/` | Kopie zapasowe i przywracanie wyglądu (`backup.sh`, `restore.sh`, `paths.sh`) oraz instalatory komponentów (`sddm_system.py` to jedyny uruchamiany przez sudo) |
| `scripts/panels/` | Skrypty plasmashell górnych pasków (zrzut, instalacja, kolejność, odinstalowanie) |
| `tools/` | Narzędzia do ikon: `make_tile.py` (kafelek z oryginalnej ikony), `make_tint.py` (eksperyment z przefarbowaniem, odrzucony) |
| `daemon/` | Usługa `tide-daemon`: sesje agentów i worktrees przez D-Bus ([opis i kontrakt](docs/tide-daemon.md)) |
| `widgets/lib/` | Wspólna biblioteka QML widżetów: karta, miernik, pierścień, wykres słupkowy z historią próbek, kropka i licznik stanu, źródło danych z usługi |
| `widgets/agents/` | Widżet agentów `org.tide.agents`: liczby sesji w panelu, lista sesji w popupie |
| `widgets/limits/` | Widżet `org.tide.limits`: pozostałe limity ChatGPT i Claude z usługi QuotaApp |
| `widgets/worktrees/` | Widżet worktrees `org.tide.worktrees`: liczba worktrees i do sprzątnięcia, lista według repozytoriów, sprzątanie scalonych |
| `widgets/system/` | Widżet `org.tide.system`: procesor, pamięć, karta graficzna i dyski z czujników ksystemstats |
| `kwin/` | Skrypt KWin `tide-agent-jump` ze skrótem Meta+A i jego instalator |
| `mk/` | Cele `make` poszczególnych komponentów (`install-*`, `uninstall-*`, katalogi testów) |
| `docs/` | Decyzje projektowe, opis usługi |

Katalogi motywu (ekran blokady, ekran ładowania) dochodzą razem z kolejnymi etapami planu.

## Agenci AI

```bash
make install-daemon install-widget-agents   # usługa i widżet (bez kopii zapasowej wyglądu)
make install-agent-jump                     # skrót Meta+A: przejście do agenta, który czeka najdłużej
make install                                # wszystko, najpierw kopia zapasowa
make test                                   # testy wszystkich komponentów
```

Gdy agent zaczyna czekać, przychodzi powiadomienie „Agent czeka: <projekt>” z przyciskiem „Przejdź” (raz na każde wejście w czekanie). Meta+A przenosi do okna sesji, która czeka najdłużej; gdy nikt nie czeka, nic się nie dzieje. Jeśli Meta+A jest już zajęty (instalator to wypisze), trzeba go zwolnić w Ustawieniach systemowych → Skróty i przypisać akcji „Tide: przejdź do agenta, który czeka najdłużej”.

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
make install-daemon install-widget-worktrees
```

Widżet „Tide · Worktrees” pokazuje w kafelku liczbę worktrees (lawenda) i liczbę gotowych do sprzątnięcia (mięta). Popup grupuje je według repozytoriów: teal — pracuje agent, mięta — scalony, masło — zmiany albo niescalone commity. „Scalony” znaczy scalony z **lokalną** gałęzią główną (master, main albo develop) — bez łączenia z serwerem. Przycisk „Wyczyść scalone” najpierw pyta o potwierdzenie i podaje, ile miejsca zwolni; usuwa tylko czyste, scalone worktrees bez agenta, bez `--force`, a gałąź kasuje wyłącznie `git branch -d`. Każdy worktree otwiera się z popupu w Konsoli albo w Riderze. Katalog projektów to `~/DevWorkspace` (zmiana: `TIDE_WORKSPACE` w środowisku usługi albo `--workspace`).

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
