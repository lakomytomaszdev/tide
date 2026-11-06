# 1: K-31/K-33 — wynik w worktree

Odtworzenie przyczyny z opisu K-37; to nie jest oryginalny dziennik produkcyjny.
Ścieżki i identyfikatory są umowne, sekrety zastąpiono przez •••.

Trafna akcja: `retry`.

Kryterium diagnozy: Rozpoznaj różnicę worktree/out; nie uznawaj braku wyniku za błąd testów ani brak logowania. Instrukcja retry musi wskazać ścieżkę out z kartki. Nie wystarczy ponowić bez instrukcji.

Doprecyzowanie koordynatora z 06.11 zachowuje PT-143: zły wynik lub niedozwolona akcja
oddają natychmiast jedną decyzję z rekomendacją, streszczeniem i tried[] jednej próby.
Limit caretakerActionLimit dotyczy błędów wykonania poprawnej akcji; wtedy decyzja
z rekomendacją i streszczeniem wszystkich prób pojawia się dopiero po limicie.
