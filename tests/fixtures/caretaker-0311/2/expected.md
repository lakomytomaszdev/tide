# 2: K-33 — niepoprawna poprawka planu

Odtworzenie przyczyny z opisu K-37; to nie jest oryginalny dziennik produkcyjny.
Ścieżki i identyfikatory są umowne, sekrety zastąpiono przez •••.

Trafna akcja: `return-plan`.

Kryterium diagnozy: Wskaż wiersz #1 z blokerem #99 spoza tabeli w plan.md oraz brak kryteriów akceptacji w tickets/1.md. To narusza standard 1.2: blokery odnoszą się do ticketów z planu, każda kartka wymaga punktu `- [ ] …`. Zaproponuj usunięcie błędnego blokera i dodanie kryteriów. Walidator dodatkowo zgłasza „plan zawiera cykl blokerów”, bo nie może wyznaczyć fal przy blokerze spoza planu; źródła nie dowodzą rzeczywistego cyklu. Poprawa ma wrócić przez plan-amend i niezależne review, następnie tylko zatwierdzenie planu.

Doprecyzowanie koordynatora z 06.11 zachowuje PT-143: zły wynik lub niedozwolona akcja
oddają natychmiast jedną decyzję z rekomendacją, streszczeniem i tried[] jednej próby.
Limit caretakerActionLimit dotyczy błędów wykonania poprawnej akcji; wtedy decyzja
z rekomendacją i streszczeniem wszystkich prób pojawia się dopiero po limicie.
