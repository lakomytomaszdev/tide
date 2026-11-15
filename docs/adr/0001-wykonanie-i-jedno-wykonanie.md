---
status: accepted
date: 2026-11-12
source: decyzja właściciela 12.11.2026 (docs/agents/masterplan/2026-11-12/audyt/DECYZJE.md), RAPORT.md §2 ADR-1
---

# ADR-1: Wykonanie poleceń i gwarancja jednego wykonania

## Kontekst

Polecenia nie-agentowe (polecenie poza piaskownicą przez `_outside_launch` w silniku, merge-check, polecenia w pudełku
przez `tasks/commands.run` i `box/command.py`) idą dziś osobnymi drogami obok umowy `Runner`, a start polecenia jest
zapisywany w dzienniku karty dopiero po `Popen`, więc „jedno wykonanie” nie jest gwarantowane. Oś wykonania ma jedną umowę, której v1 daje
implementację hostową, v1.1 pudełkową, v2 wieloprojektową.

## Decyzja

- `Runner.run_command(request) -> CommandHandle` jest **jedyną** drogą poleceń nie-agentowych.
- `HostRunner` = dzisiejsze ciało `_outside_launch` + izolacja jako przejściowa usługa
  `systemd-run --user --wait --collect -p …` z opakowaniem `tide-step` i plikiem `exit-code` (model `BoxRunner`).
- `BoxRunner.run_command` wchłania `tasks/commands.run` i `box/command.py`.
- `_start_outside`, `_merge_check_run` i CLI wołają runner; odmowa `box-outside` znika.
- **Protokół jednego wykonania** obowiązuje oba runnery (`HostRunner`, `BoxRunner`) i CLI:
  1. trwały identyfikator wykonania (w raporcie: „identyfikator próby”) i zdarzenie zamiaru `command.intent`
     **przed** uruchomieniem;
  2. rezerwacja budżetu (ADR-3) w tym samym zapisie;
  3. potwierdzenie startu — `command.started` z jednostką — po uzyskaniu uchwytu;
  4. wynik — `command.finished` z kodem wyjścia **z pliku `exit-code`**, nie z kodu klienta;
  5. odzyskiwanie po restarcie: zamiar bez startu → sprawdzenie jednostki po identyfikatorze; nigdy ponowne
     uruchomienie (`Popen`) bez dowodu braku procesu.
- Wydzielenie `_outside*` i `_merge_check*` do `commands.py` jest częścią implementacji `run_command`, nie osobnym
  refaktorem — według reguły granic modułów [ADR-6](0006-granice-modulow-silnika.md).

## Konsekwencje

- Zamyka się szczelina między uruchomieniem procesu a zapisem jego startu; kryterium v1 nr 8 („restart podczas
  polecenia = 0 podwójnych operacji”) dostaje mechanizm.
- **Odbiór M2 na obu runnerach z realnym krokiem w pudełku** — `box.enabled=true` bez kroków mierzy wydajność, nie
  izolację.
- v1.1 = pudełko tym samym wejściem; v2 = profile .NET/Docker bez zmian silnika.

**Co temu dziś przeczy** (zniknie w tej kolejności, w K-109 etap 1):

1. `_outside_launch` w silniku i zapis startu (`outside.started`) po `Popen` — zastąpione protokołem z zamiarem
   przed startem;
2. odmowa `box-outside` dla kroku w pudełku — znika, gdy `BoxRunner.run_command` przyjmuje polecenia.

## Karta wykonawcza i kamień

K-109 etap 1 (`run_command` + protokół + `commands.py`), kamień **M2**.

## Pytania otwarte (dla K-109)

- Rezerwacja budżetu w kroku 2 zależy od tego, gdzie leży pula modułów dla pudełka — pytanie z
  [ADR-3](0003-budzet-pracy.md).
