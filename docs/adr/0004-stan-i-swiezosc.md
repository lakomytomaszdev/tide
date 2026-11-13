---
status: accepted
date: 2026-11-12
source: decyzja właściciela 12.11.2026 (docs/agents/masterplan/2026-11-12/audyt/DECYZJE.md), RAPORT.md §2 ADR-4
---

# ADR-4: Stan i świeżość — jedna wersja dziennika karty

## Kontekst

Tick usługi wykonuje trzy ścisłe odczyty dziennika na kartę. Tło (diagnoza P6 raportu, tylko
jako kontekst, nie decyzja): stan istnieje dziś w czterech kopiach z różnymi granicami unieważnienia — dziennik
usługi i jego lustro na Tablicy, `FlowState`/`Snapshot`, plan w kilku miejscach oraz pamięci jednego ticku.

## Decyzja

- **Kontrakt PT-411 bez zmian:** wersja dziennika karty = `(źródło, st_dev, st_ino, st_size, st_mtime_ns,
  st_ctime_ns, generation, ostatni seq)` z `fstat` otwartego deskryptora; przyrost z walidacją; pełny odczyt co
  10 min.
- **Błąd nigdy nie jest pamięcią.**
- Bramki przed skutkiem **czytają świeżo** (`unchanged`, `append_complete`).
- **PT-415 i PT-416 korzystają z tej samej wersji** co PT-411 — to już jeden mechanizm, nie trzy; łączenie ticketów
  z wersji 1 raportu jest wycofane.
- Karta w spoczynku poza pętlą (PT-412).
- Migawka różnicowa (`version` już jest); **nowe widoki przez osobne metody D-Bus**.

## Konsekwencje

PT-411, PT-415 i PT-416 korzystają z jednej wersji; PT-412 (karta w spoczynku poza pętlą) jest osobną zmianą.
Nowe widoki idą przez osobne metody D-Bus.

**Co temu dziś przeczy:** trzy ścisłe odczyty na kartę na tick — znikają z K-105 (M0).

## Karta wykonawcza i kamień

K-105, kamień **M0**.

## Pytania otwarte (dla K-105)

Brak decyzji otwartej. Udział tych odczytów w CPU usługi jest w raporcie `[hipoteza]` — rozstrzyga pomiar
„przed/po” K-105, nie ten ADR.
