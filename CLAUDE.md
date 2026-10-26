# Tide

Własny motyw KDE Plasma 6 (KDE neon, Wayland, dwa monitory) z widżetami do pracy z agentami AI. Opis i struktura: `README.md`, uzasadnienia decyzji: `docs/decisions.md`.

## Zasady projektu

- Commity i nazwy gałęzi po angielsku, bez podpisów i wzmianek o AI. Push tylko na wyraźną prośbę.
- Rozmowa, dokumentacja i komentarze w kodzie po polsku.
- Wygląd zmieniamy wyłącznie przez `make install`, który najpierw robi kopię zapasową. Nigdy nie edytujemy ręcznie plików w `~/.local/share` ani `~/.config`.
- Kolory, promienie i czcionki pochodzą z palety w `theme/palette/`. Generatory czytają paletę, wartości nie są wpisywane na sztywno.
- Pakiety apt instalujemy tylko po symulacji (`apt-get -s install`), która nie może podnosić Qt ani KDE Frameworks.
- Ekran blokady i SDDM testujemy w trybach testowych (`kscreenlocker_greet --testing`, `sddm-greeter-qt6 --test-mode`), zanim trafią do systemu.

## Agent skills

### Issue tracker

GitHub Issues w prywatnym repozytorium lakomytomaszdev/plasma-tide (CLI `gh`). See `docs/agents/issue-tracker.md`.

### Domain docs

Single-context: `CONTEXT.md` i `docs/adr/` w katalogu głównym. See `docs/agents/domain.md`.
