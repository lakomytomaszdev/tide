# Tide

Własny motyw KDE Plasma 6 (KDE neon, Wayland, dwa monitory) z widżetami do pracy z agentami AI. Opis i struktura: `README.md`, uzasadnienia decyzji: `docs/decisions.md`.

## Zasady projektu

- Commity i nazwy gałęzi po angielsku, bez podpisów i wzmianek o AI. Push tylko na wyraźną prośbę.
- Rozmowa, dokumentacja i komentarze w kodzie po polsku.
- Wygląd zmieniamy wyłącznie przez `make install`, który najpierw robi kopię zapasową. Nigdy nie edytujemy ręcznie plików w `~/.local/share` ani `~/.config`.
- Kolory, promienie i czcionki pochodzą z palety w `theme/palette/`. Generatory czytają paletę, wartości nie są wpisywane na sztywno.
- Pakiety apt instalujemy tylko po symulacji (`apt-get -s install`), która nie może podnosić Qt ani KDE Frameworks.
- Ekran blokady i SDDM testujemy w trybach testowych (`kscreenlocker_greet --testing`, `sddm-greeter-qt6 --test-mode`), zanim trafią do systemu.

## Testy podczas pracy

W trakcie ticketu uruchamiaj testy dobrane do zmiany (`make test-cardflow`, `make test-daemon`,
`make test-board` albo `make test-changed BASE=<baza>`). Pełny `make test` wykonaj raz przed
oddaniem ticketu. Domyślnie moduły biegną w liczbie procesów równej liczbie rdzeni minus dwa;
`JOBS=N` zmienia limit. Lista zestawów: `tools/test_suites.py`, instrukcja i izolacja:
[docs/testing.md](docs/testing.md).

## Agent skills

### Issue tracker

GitHub Issues w prywatnym repozytorium `lakomytomaszdev/plasma-tide` (CLI `gh`) pozostają dostępne dla
ogólnych zgłoszeń. Specyfikacje, plany i tickety przepływu karty są plikami w repozytorium; źródłem ich stanu
jest plan. Zasady: `docs/agents/issue-tracker.md`.

### Domain docs

Single-context: `CONTEXT.md` i `docs/adr/` w katalogu głównym. See `docs/agents/domain.md`.

## Plan pracy agentów (Tide)

Standard: `~/DevWorkspace/plasma-tide/docs/agent-plan-standard.md`.

1. Sesja koordynująca, która dzieli pracę na tickety, prowadzi plan `docs/agents/plans/YYYY-MM-DD-<slug>.md`, commitowany razem z pracą albo osobnym commitem `docs(plan): …`.
2. Frontmatter: `tide-plan: 1`, `title`, `tracker: local`, `tracker-url` i `tracker-project` puste, `ticket-prefix: PT`, `coordinator` (`claude`|`codex`), `session` (ID sesji, jeśli znane), `status` (`active`|`paused`|`done`), `updated` (czas ISO 8601).
3. Tabela `| Ticket | Tytuł | Stan | Gałąź | Agent | Uwagi |`; `Stan` ∈ `todo`, `w-toku`, `review`, `czeka`, `gotowe`, `odrzucone`. Tabelę i `updated` aktualizuj przy każdym delegowaniu, wyniku, review i pytaniu do użytkownika.
4. Delegowanie: opis/tytuł subagenta zaczyna się od ID ticketu (`PT-7: implement …`, `PT-7: review`); subagent Codex ma ID ticketu w zadaniu i w nickname. Gałąź zawiera ID małymi literami (`feat/pt-7-…`). Pytanie do użytkownika wymienia ID ticketu.
5. Plan przepływu karty nie korzysta z zewnętrznego trackera: ID ticketów nadaje plan (`PT-1`, `PT-2`, …), numeracja jest ciągła we wszystkich planach repozytorium (nowy numer = największy istniejący + 1, nowy plan nie zaczyna od 1), a plan jest źródłem stanu dla tablicy Tide i porannego raportu. GitHub Issues nie są źródłem planu.
6. Projekt `TIDE` w YouTracku to wyłącznie archiwum zrobionej pracy (TIDE-1…31, 26–27.10.2026), nie źródło planu. Prefiksu `TIDE` nie używamy w planach — to klucz projektu w YouTracku (reguła „jedno źródło numerów” w standardzie).

## Przepływ karty

Słownictwo projektu: `CONTEXT.md`. Pełny format planu i kartki oraz kontrakt kroku:
`docs/agent-plan-standard.md`; działanie usługi: `docs/tide-daemon.md`. Karta Tablicy prowadzi dziennik,
gałąź karty, osobne tickety, review u innego modelu i decyzje użytkownika w panelu. Przy nowej pracy wybierz
„Od razu”, „Lekkie”, „Pełne” albo „Mam plan” zgodnie z potrzebą; Pełne planowanie w nowym przepływie jest
przewidziane na drugi etap, do tego czasu rozmowa planująca pozostaje w Konsoli. Zanim zaczniesz ticket,
przeczytaj jego kartkę i plan. Gdy uruchamiasz krok w tle, zapisz wynik w `result.json` według kontraktu.
Nie wykonuj push ani scalenia do gałęzi głównej bez wyraźnego polecenia użytkownika.
