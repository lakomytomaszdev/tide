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
| `theme/palette/` | Paleta wariantu w JSON, jedyne źródło kolorów dla generatorów |
| `theme/wallpaper/` | Generator tapety z palety (jedno płótno na oba monitory) |
| `scripts/` | Kopie zapasowe i przywracanie wyglądu (`backup.sh`, `restore.sh`, `paths.sh`), instalatory komponentów |
| `tools/` | Narzędzia do ikon: `make_tile.py` (kafelek z oryginalnej ikony), `make_tint.py` (eksperyment z przefarbowaniem, odrzucony) |
| `daemon/` | Usługa `tide-daemon`: sesje agentów i worktrees przez D-Bus ([opis i kontrakt](docs/tide-daemon.md)) |
| `widgets/lib/` | Wspólna biblioteka QML widżetów: karta, miernik, pierścień, kropka i licznik stanu, źródło danych z usługi |
| `widgets/agents/` | Widżet agentów `org.tide.agents`: liczby sesji w panelu, lista sesji w popupie |
| `widgets/worktrees/` | Widżet worktrees `org.tide.worktrees`: liczba worktrees i do sprzątnięcia, lista według repozytoriów, sprzątanie scalonych |
| `mk/` | Cele `make` poszczególnych komponentów (`install-*`, `uninstall-*`, katalogi testów) |
| `docs/` | Decyzje projektowe, opis usługi |

Katalogi motywu (kolory, styl Plasmy, dekoracja okien, ikony, ekran blokady, SDDM) dochodzą razem z kolejnymi etapami planu.

## Agenci AI

```bash
make install-daemon install-widget-agents   # usługa i widżet (bez kopii zapasowej wyglądu)
make install                                # wszystko, najpierw kopia zapasowa
make test                                   # testy wszystkich komponentów
```

Widżet „Tide · Agenci” dodaje się do panelu z listy widżetów. Po aktualizacji już dodanego widżetu Plasma wczyta nowy kod dopiero po `systemctl --user restart plasma-plasmashell`. Kolory biblioteki QML są generowane z palety przy składaniu pakietu (`widgets/stage.py` → `Tokens.qml`).

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
