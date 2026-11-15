---
status: accepted
date: 2026-11-12
source: decyzja właściciela 12.11.2026 (docs/agents/masterplan/2026-11-12/audyt/DECYZJE.md), RAPORT.md §2 ADR-5
---

# ADR-5: Autoryzacja wyników kroków i operacje Git

## Kontekst

Werdykt review jest już przypięty do commita (`reviewTip` → `expected_tip`), ale nośnikiem werdyktu jest zapisywalny
`result.json`, a blokada gita jest kluczowana ścieżką, nie repozytorium. Ten ADR to kontrakt z kartami wykonawczymi, punkt po punkcie.

## Decyzja

| Punkt | Decyzja | Karta wykonawcza i kamień |
|---|---|---|
| (a) | **Werdykt przypięty do SHA i próby** („próba” w brzmieniu raportu; w słowniku próba to ponowienie kroku) — jest (`reviewTip` → `expected_tip`); formalnie zapisać jako warunek scalenia. | K-109 etap 2, **M2** |
| (b) | **Pole `source`** (panel / D-Bus / opiekun / reguła) w zdarzeniach `step.decision` i `review.decision`. | K-108, **M2** |
| (c) | **Chroniony kanał werdyktu i niezmienne wejście review**: wynik review zapisuje usługa z sesji recenzenta do katalogu poza zapisem kroku; hash wejścia review w zdarzeniu. Dziura jest ta sama na hoście i w pudełku. | **K-109 etap 2, M2 — dla hosta**; K-83 (v1.1) tylko dowodzi tego dla `executor: box` |
| (d) | **Jedna blokada gita po `git-common-dir`** (`lock_for(repository_key(repo))`). K-66a i K-107 #15 jej używają. | K-110 ticket 1, **M3** (K-110 dokłada operacje pod tą blokadą) |
| (e) | **`merge-tree` przez istniejące `gitops.prepare_merge_tree`**, bez nowej drogi. | K-110, **M3** |

## Konsekwencje

- Kanał werdyktu nie czeka na pudełko: K-83 nie jest jego kartą wykonawczą, tylko dowodem dla `executor: box`.
- Karty dokładające operacje gita (K-110, K-66a, K-107 #15) używają tej samej blokady i istniejącego
  `prepare_merge_tree`.

**Co temu dziś przeczy** (w kolejności planu):

1. zapisywalny `result.json` jako nośnik werdyktu — znika z chronionym kanałem (K-109 etap 2, M2);
2. blokada gita po ścieżce worktree — znika z K-110 ticket 1 (M3).

## Pytania otwarte

Brak decyzji otwartej. Postać zapisu chronionego kanału (katalog, format, sesja recenzenta) ustala plan K-109 etap 2
w ramach punktu (c).
