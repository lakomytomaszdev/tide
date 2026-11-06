# Scenariusz 03.11 — ręczna ocena jakości

Zestaw odtworzony na podstawie przyczyn opisanych w kartce PT-156 karty K-37.
Nie jest eksportem produkcji ani pomiarem skuteczności modelu. Każdy katalog zawiera
odtworzony journal.jsonl, wycinek step.log oraz expected.md z kryterium diagnozy.
Identyfikatory kart wskazują źródło przyczyny; ścieżki są umowne. ••• oznacza maskowanie.
Nie kopiować tych dzienników do działającej tablicy ani nie uruchamiać ich jako migracji stanu.

| Przypadek | Trafna akcja | Jedyna oczekiwana decyzja użytkownika |
| --- | --- | --- |
| 1 | retry z instrukcją zapisu do out | brak |
| 2 | return-plan → plan-amend → review | zatwierdzenie planu |
| 3 | return-plan z zatwierdzoną bazą i nowym ticketem | zatwierdzenie różnicy |
| 4a | retry z report | brak |
| 4b | retry-stage z report | brak |

Procedura: docs/tide-daemon.md, „Scenariusz 03.11 — ocena na prawdziwym modelu”.
Oceniający czyta expected.md dopiero po zapisaniu odpowiedzi modelu.
Żaden prawdziwy wykonawca nie jest wywoływany przez make test.
