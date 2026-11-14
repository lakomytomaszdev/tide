---
status: accepted
date: 2026-11-12
source: decyzja właściciela 12.11.2026 (docs/agents/masterplan/2026-11-12/audyt/DECYZJE.md), RAPORT.md §3 i §7 pkt 2
---

# ADR-6: Granice modułów silnika — wydzielanie odpowiedzialności z jawnymi wejściami

Zapis istniejącej decyzji z audytu, nie nowa decyzja.

## Kontekst

`engine.py` rośnie z każdą kartą v1; dotychczasowe podziały (bazy `CaretakerChat`, `PlanEditing`) używają wspólnego
`self` i nie zmieniają grafu importów, a pomocnicy importują `FlowError`/`FlowDeferred` z silnika. Mechaniczny podział silnika przed v1
nie leczy przyczyn i — mierzony liniami — nagradza przenoszenie, nie odsprzęganie.

## Decyzja

- **Nie rozbijamy silnika przed v1** i nie otwieramy okna na mixiny.
- **Reguła dla kart silnika** (K-109, K-108, K-106, K-79, K-110, K-107, K-95, K-111): każda karta wydziela
  **odpowiedzialność, której dotyka, do modułu z jawnymi wejściami** — dane w parametrach, wynik jako wartość;
  wzór `heavyqueue.py`, `ticketlimit.py`, `stepqueue.py`. W silniku zostaje **cienki wrapper** (zachowuje punkty
  patchowania testów).
- **Bez mixinów ze wspólnym `self`.**
- **Nowy moduł nie importuje `engine`.**
- **Miara: zależności testów, nie linie.** Kryterium w kartce: liczba modułów testów wybieranych dla zmiany w nowym
  module < liczba dla `engine.py` — pomiar `tools/test_dependencies.py` w notce (kolumna „sięga silnika” raportu
  zależności); linie `engine.py` po scaleniu podajemy tylko informacyjnie.
- **Kto co wydziela:**

  | Karta | Odpowiedzialność | Moduł |
  |---|---|---|
  | okno porządkowe (K-112) | `FlowError`, `FlowDeferred`; odczyt wyniku kroku (`_raw_result`, `_result`, `_review_result`, `_claude_denied_command`); pomiar bazowy zależności | `cardflow/errors.py`, `cardflow/stepresults.py`, `tools/test_dependencies.py` |
  | K-109 | `_outside*` + `_merge_check*` jako część implementacji `run_command` ([ADR-1](0001-wykonanie-i-jedno-wykonanie.md)), nie osobny refaktor | `commands.py` |
  | K-79 | `_caretaker*` + `_guard*` | `caretaking.py` |
  | K-108 | `_recover*` (wznowienia) | — |
  | K-76 (po odbiorze v1) | reszta: jądro `tick`/`_advance`/`_start`/`_resume`, `plan*`, kod K-38, `coordination/` | — |

- Okno porządkowe nie dotyka `tick`, `_advance*`, `_start*`, `_enter*`, `_resume*`, `_resolve*`, `__init__`,
  schematu zdarzeń ani `FlowState`.

## Konsekwencje

- Odrzucone: cel liczby linii (≤ 7 400 był nierealny), „netto maleje” jako kryterium i mixiny ze wspólnym `self`.
- Ryzyko fałszywego sukcesu (mniej linii, te same zależności) ogranicza miara zależnościami.
- Kryterium „gotowe” okna porządkowego: testy cardflow bez zmiany asercji; zero importu `engine` z nowych modułów;
  pomiar bazowy zależności w notce; czas `make test-cardflow` przy stałym `JOBS` nie gorszy niż 10 %.

**Co temu dziś przeczy** (w kolejności planu):

1. pomocnicy importujący `FlowError`/`FlowDeferred` i odczyt wyniku kroku z silnika — znika w oknie porządkowym
   (K-112);
2. `_outside*` i `_merge_check*` w silniku — K-109 (M2);
3. `_recover*` w silniku — K-108 (M2);
4. `_caretaker*` i `_guard*` w silniku — K-79 (M3);
5. jądro, `plan*`, kod K-38 i `coordination/` w silniku — K-76 po odbiorze v1.

## Karta wykonawcza i kamień

Okno porządkowe (K-112) po M0; reguła obowiązuje każdą kartę silnika do v1; K-76 po M5.

## Pytania otwarte

- **Dla K-79 i K-76:** raport wymienia bazy `CaretakerChat` i `PlanEditing` jako wadę, ale nie przypisuje ich
  wprost żadnej karcie; przypisanie wynika tylko z grup `_caretaker*` (K-79) i `plan*` (K-76) — do potwierdzenia
  w planach tych kart.
- **Dla K-76:** `tools/test_suites.py` mapuje cały katalog `cardflow/` na pełny zestaw testów; raport nie przypisuje
  zmiany tego mapowania żadnej karcie.
