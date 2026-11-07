# 3: K-16 — luka w zatwierdzonym planie

Odtworzenie przyczyny z opisu K-37; to nie jest oryginalny dziennik produkcyjny.
Ścieżki i identyfikatory są umowne, sekrety zastąpiono przez •••.

Trafna akcja: `return-plan`.

Kryterium diagnozy: Połącz decyzję A z brakującym ticketem. Odesłanie musi korzystać z zatwierdzonej bazy; rozpoczęte tickety zostają identyczne. Użytkownik zatwierdza wyłącznie różnicę z nowym ticketem.

Doprecyzowanie koordynatora z 06.11 zachowuje PT-143: zły wynik lub niedozwolona akcja
oddają natychmiast jedną decyzję z rekomendacją, streszczeniem i tried[] jednej próby.
Limit caretakerActionLimit dotyczy błędów wykonania poprawnej akcji; wtedy decyzja
z rekomendacją i streszczeniem wszystkich prób pojawia się dopiero po limicie.
