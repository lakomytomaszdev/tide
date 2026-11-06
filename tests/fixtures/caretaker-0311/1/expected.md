# 1: K-31/K-33 — wynik w worktree

Odtworzenie przyczyny z opisu K-37; to nie jest oryginalny dziennik produkcyjny.
Ścieżki i identyfikatory są umowne, sekrety zastąpiono przez •••.

Trafna akcja: `retry`.

Kryterium diagnozy: Rozpoznaj różnicę worktree/out; nie uznawaj braku wyniku za błąd testów ani brak logowania. Instrukcja retry musi wskazać ścieżkę out z kartki. Nie wystarczy ponowić bez instrukcji.

Dla wariantu „opiekun zawodzi” wymaganie PT-156 to jedna decyzja z rekomendacją,
streszczeniem i tried[] po caretakerActionLimit działaniach. Obecny kontrakt PT-143
oddaje zły wynik/niedozwoloną akcję natychmiast — to jawna luka, nie zaliczony przypadek.
