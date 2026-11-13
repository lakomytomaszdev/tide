---
status: accepted
date: 2026-11-12
source: decyzja właściciela 12.11.2026 (docs/agents/masterplan/2026-11-12/audyt/DECYZJE.md), RAPORT.md §2 ADR-3
---

# ADR-3: Budżet pracy — jedna bramka przyjęcia ciężkiej pracy

## Kontekst

Pula modułów testów (`MachineSlots`) istnieje, ale polecenia uruchamiane w kroku trafiają do drugiej puli
(`env -u XDG_RUNTIME_DIR` gubi katalog blokad), pojemność jest stałą rdzenie−2, liczba ciężkich przebiegów nie ma
limitu, a przebiegi spoza usługi nie są w ewidencji `heavyqueue`.

## Decyzja

- Jedna bramka przyjęcia ciężkiej pracy **przed wyborem runnera** (`heavyqueue.admit`) z osobnymi budżetami:
  - miejsca u dostawcy (`providerSlots`),
  - liczba ciężkich przebiegów (`heavyLimit`),
  - moduły testów (`MachineSlots`).
- Trzy zmiany konieczne:
  1. **jedna pula modułów** — `TIDE_TEST_SLOTS_DIR` ustawiany przez usługę i **przekazywany przez `agentcmd` i do
     pudełka** (dziś `env -u XDG_RUNTIME_DIR` ją gubi); w pudełku pula musi być po stronie hosta;
  2. **pojemność puli liczona z `loadavg`** i rezerwy, nie ze stałej rdzenie−2;
  3. **przebiegi spoza usługi widoczne w ewidencji** (pula hosta już je obejmuje; `heavyqueue` nie — do K-52).
- `OUTSIDE_PER_PROJECT` / `OUTSIDE_TOTAL` zostają limitem lekkich.

## Konsekwencje

- Rezerwacja budżetu jest krokiem 2 protokołu jednego wykonania ([ADR-1](0001-wykonanie-i-jedno-wykonanie.md)),
  w tym samym zapisie co zamiar.
- Klasyfikacja ciężkie/lekkie idzie przez `policy.classify` ([ADR-2](0002-uprawnienia.md)).

**Co temu dziś przeczy** (w kolejności planu):

1. brak limitu liczby ciężkich przebiegów — znika z K-39 (`heavyLimit`, M0);
2. dwie pule modułów — znikają z przekazaniem `TIDE_TEST_SLOTS_DIR` przez `agentcmd` (K-109 etap 1 albo K-100 —
   patrz pytania) i z rozstrzygnięciem puli w pudełku (K-109);
3. stała pojemność i przebiegi spoza usługi poza ewidencją `heavyqueue` — zmiany 2 i 3 (K-109 / K-52).

## Karta wykonawcza i kamień

K-39 + K-109, kamienie **M0–M2**; ewidencja przebiegów spoza usługi w `heavyqueue` → K-52 (v1.1).

## Pytania otwarte

- **Dla K-109: pula modułów w pudełku** — montaż katalogu blokad albo rezerwacja miejsc przed `incus exec`; plan
  K-109 wybiera wariant i dowodzi go testem. `[NIESPRAWDZONE wykonalność montażu]`.
- **Dla K-109 / K-52:** raport przypisuje lukę „pojemność bez reszty obciążenia” (zmiana 2) łącznie do K-109/K-52,
  bez wskazania jednej karty — rozstrzyga plan K-109.
- **Dla K-109 / K-100:** raport w tabeli etapów dopuszcza przekazanie `TIDE_TEST_SLOTS_DIR` przez `agentcmd` jako
  ticket w K-100 albo w K-109 etap 1, a w werdykcie kart przypisuje je K-109 — rozbieżność do rozstrzygnięcia przy
  planie tej karty, która zacznie pierwsza.
