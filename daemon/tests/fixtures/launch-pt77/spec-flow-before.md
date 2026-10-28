---
tide-flow: 3
mode: spec
---
Karta {card_id}: {title}
Projekt: {project}
Repozytorium: {repo}
Gałąź: {branch}
Plany i specyfikacje: {docs_dir}{projects}

Opis karty:

{description}

Tryb „Tylko specyfikacja”: dopytujesz i piszesz specyfikację — nic więcej. Bez zmian w kodzie, bez
gałęzi i worktree, bez planu i ticketów.

Dokumenty AI: {docs_note}
Commit: {spec_commit}
Istniejąca specyfikacja karty: {spec}

1. Rozejrzyj się w kodzie i dokumentacji projektu tylko do odczytu, żeby pytać konkretnie.
2. Grilling: użyj skilla `grilling`, jeśli go masz (Claude Code). Jeśli nie (Codex) — przepytaj mnie
   słowami: po jedno–trzy pytania naraz, aż znasz cel, zakres, przypadki brzegowe, kryteria akceptacji
   i to, czego NIE robimy. Nie zgaduj.
3. Zapisz specyfikację po polsku w `{spec_file}` z frontmatterem:
   ---
   card: {card_id}
   status: szkic
   ---
   Treść: cel, zakres, poza zakresem, kryteria akceptacji, decyzje z rozmowy, otwarte pytania, ryzyka
   i propozycja podziału na kroki (bez numerów ticketów). Sprawdzenie pracy po wdrożeniu:
   {check_label} — przy teście ręcznym dopisz sekcję „Co przetestować ręcznie i jak” (kroki
   i oczekiwany wynik), przy `auto` napisz, co sprawdzą testy automatyczne i review AI.
   Zanim pokażesz mi specyfikację, zleć jej review AI (podagent: luki, sprzeczności, niesprawdzalne
   kryteria) i popraw, co znajdzie.
4. Gdy zaakceptuję specyfikację, zmień `status: szkic` na `status: gotowa` — wtedy Tide oznaczy kartę
   „specyfikacja gotowa” i odłoży ją do Backlogu.
5. Napisz mi, że specyfikacja jest gotowa i gdzie leży. Nie implementuj.
