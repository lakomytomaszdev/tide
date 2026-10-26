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
| `scripts/` | Kopie zapasowe i przywracanie wyglądu (`backup.sh`, `restore.sh`, `paths.sh`) |
| `tools/` | Narzędzia do ikon: `make_tile.py` (kafelek z oryginalnej ikony), `make_tint.py` (eksperyment z przefarbowaniem, odrzucony) |
| `docs/` | Decyzje projektowe |

Katalogi motywu (kolory, styl Plasmy, dekoracja okien, ikony, ekran blokady, SDDM), widżetów i usługi `tide-daemon` dochodzą razem z kolejnymi etapami planu.

## Kopie zapasowe

Przed każdą zmianą wyglądu powstaje kopia w `~/.local/share/plasma-tide/backups`.

```bash
make backup NAME=przed-zmiana   # nowa kopia
make backups                    # lista kopii
make restore NAME=przed-tide    # powrót do wyglądu sprzed projektu
```

`restore` sam robi najpierw kopię bieżącego stanu, więc przywracanie też da się cofnąć. Po przywróceniu warto się wylogować i zalogować ponownie.
