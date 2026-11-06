# 4b: Tide — błąd przygotowania etapu

Odtworzenie przyczyny z opisu K-37; to nie jest oryginalny dziennik produkcyjny.
Ścieżki i identyfikatory są umowne, sekrety zastąpiono przez •••.

Trafna akcja: `retry-stage` z `report`.

Kryterium diagnozy: Odróżnij flow.error bez kroku-sprawcy od błędu ticketu. retry-stage z report; nie retry zakończonego ticketu. Jednorazowy błąd bez drugiego przebiegu nie wystarcza.

Dla wariantu „opiekun zawodzi” wymaganie PT-156 to jedna decyzja z rekomendacją,
streszczeniem i tried[] po caretakerActionLimit działaniach. Obecny kontrakt PT-143
oddaje zły wynik/niedozwoloną akcję natychmiast — to jawna luka, nie zaliczony przypadek.
