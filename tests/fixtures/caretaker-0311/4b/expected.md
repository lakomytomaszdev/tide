# 4b: Tide — błąd przygotowania etapu

Odtworzenie przyczyny z opisu K-37; to nie jest oryginalny dziennik produkcyjny.
Ścieżki i identyfikatory są umowne, sekrety zastąpiono przez •••.

Trafna akcja: `retry-stage` z `report`.

Kryterium diagnozy: Odróżnij flow.error bez kroku-sprawcy od błędu ticketu. retry-stage z report; nie retry zakończonego ticketu. Jednorazowy błąd bez drugiego przebiegu nie wystarcza.

Doprecyzowanie koordynatora z 06.11 zachowuje PT-143: zły wynik lub niedozwolona akcja
oddają natychmiast jedną decyzję z rekomendacją, streszczeniem i tried[] jednej próby.
Limit caretakerActionLimit dotyczy błędów wykonania poprawnej akcji; wtedy decyzja
z rekomendacją i streszczeniem wszystkich prób pojawia się dopiero po limicie.
