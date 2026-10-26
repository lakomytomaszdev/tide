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
| `theme/lookandfeel/` | Motyw globalny „Tide” (`org.tide.desktop`): wskazania na składniki Tide i podglądy |
| `theme/lockscreen/` | Ekran blokady Tide jako pakiet powłoki Plasmy (QML na bazie ekranu Plasmy 6.8), zrzuty z prawdziwego greetera |
| `theme/icons/` | Nakładka ikon „Tide” i polecenie `tide-icons` (audyt, synchronizacja, podgląd) |
| `scripts/` | Kopie zapasowe i przywracanie wyglądu (`backup.sh`, `restore.sh`, `paths.sh`) oraz instalatory komponentów |
| `scripts/panels/` | Skrypty plasmashell górnych pasków (zrzut, instalacja, kolejność, odinstalowanie) |
| `tools/` | Narzędzia do ikon: `make_tile.py` (kafelek z oryginalnej ikony), `make_tint.py` (eksperyment z przefarbowaniem, odrzucony) |
| `daemon/` | Usługa `tide-daemon`: sesje agentów i worktrees przez D-Bus ([opis i kontrakt](docs/tide-daemon.md)) |
| `widgets/lib/` | Wspólna biblioteka QML widżetów: karta, miernik, pierścień, kropka i licznik stanu, źródło danych z usługi |
| `widgets/agents/` | Widżet agentów `org.tide.agents`: liczby sesji w panelu, lista sesji w popupie |
| `widgets/limits/` | Widżet `org.tide.limits`: pozostałe limity ChatGPT i Claude z usługi QuotaApp |
| `widgets/worktrees/` | Widżet worktrees `org.tide.worktrees`: liczba worktrees i do sprzątnięcia, lista według repozytoriów, sprzątanie scalonych |
| `kwin/` | Skrypt KWin `tide-agent-jump` ze skrótem Meta+A i jego instalator |
| `mk/` | Cele `make` poszczególnych komponentów (`install-*`, `uninstall-*`, katalogi testów) |
| `docs/` | Decyzje projektowe, opis usługi |

Katalogi motywu (ekran startowy, SDDM) dochodzą razem z kolejnymi etapami planu.

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

## Motyw globalny i ekran blokady

Motyw globalny „Tide” (`org.tide.desktop`) spina w Ustawieniach systemowych to, co instalują pozostałe części: schemat TideMist, styl Plasmy Tide, dekorację Aurorae Tide (przyciski po prawej: minimalizuj, maksymalizuj, zamknij), ikony Tide, kursor Nordic-cursors i czcionki Geist. Nie ma w nim skryptów układu ani tapety — panele i tapety ustawiają `scripts/panels.py` i `scripts/wallpaper.py`.

`make install` (cel `install-lookandfeel`) kopiuje motyw do `~/.local/share/plasma/look-and-feel/org.tide.desktop` i zapisuje tylko wybór motywu (`kdeglobals [KDE] LookAndFeelPackage`, wyłączona automatyczna zmiana motywu) oraz tapetę ekranu blokady (obraz Tide głównego monitora w `kscreenlockerrc`). **Nie wywołuje `plasma-apply-lookandfeel`** i nie dotyka paneli. Poprzednie wartości są w `~/.local/state/tide/lookandfeel-previous.json`, `make uninstall` je przywraca.

W Ustawieniach systemowych → Motyw globalny można kliknąć „Tide”, ale **nie zaznaczać „Układ pulpitu i okien”**: Plasma kasuje wtedy całą konfigurację paneli (`plasma-org.kde.plasma.desktop-appletsrc`) i ładuje układ Breeze. Ratunek: `make restore NAME=<kopia>` i ponowne `make install`.

**Ekran blokady.** W Plasmie 6.8 greeter (`kscreenlocker_greet`) bierze ekran blokady wyłącznie z pakietu powłoki (`plasma/shells/<powłoka>/contents/lockscreen`), a powłokę wybiera tak samo jak plasmashell (`plasmashellrc [Shell] ShellPackage`, domyślnie `org.kde.plasma.desktop`). Katalog `lockscreen/` w motywie globalnym jest ignorowany, a klucz `kscreenlockerrc [Greeter] Theme` nie istnieje. Dlatego ekran Tide jest osobnym pakietem powłoki `org.tide.desktop`, który ma tylko `lockscreen/`, a resztę bierze z `org.kde.plasma.desktop`. Instalacja kładzie go do `~/.local/share/plasma/shells/`, ale **prawdziwa blokada nadal używa ekranu Plasmy** (już z tapetą, kolorami i czcionkami Tide) — sposób włączenia ekranu Tide czeka na decyzję (docs/decisions.md, punkt 24).

```bash
make lockscreen-screenshot   # zrzut prawdziwego greetera w zagnieżdżonym KWin, bez ekranu → build/lockscreen.png
make lockscreen-test         # greeter w trybie testowym jako okno (XWayland); hasło zamyka okno
make lookandfeel-build       # sam motyw globalny w build/lookandfeel
```

Wygląd z makiety: przyciemniona tapeta Mist (rozmyta przy widocznym polu hasła), zegar Geist 96 px i data („poniedziałek, 26 października”) w górnej części, logo „trzy fale”, nazwa użytkownika i pole hasła na karcie z tealową obwódką, na dole klawiatura, uśpienie i zmiana użytkownika jako same ikony. Linia „2 agenci pracują · 1 czeka” pochodzi z tide-daemon przez D-Bus sesji (greeter działa jako użytkownik i dziedziczy magistralę po KWin); pokazuje tylko liczby, bez nazw projektów, i znika, gdy usługi nie ma albo nikt nie pracuje. Gdy QML ekranu Tide się nie wczyta, greeter sam pokazuje awaryjny ekran KDE (sprawdzane w testach). Licencja i pochodzenie plików: `NOTICE` w pakiecie (GPL-2.0-or-later, jak ekran Plasmy).

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
