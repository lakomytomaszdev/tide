# Tracker pracy: pliki w repozytorium

Specyfikacje, plany i tickety przepływu karty są plikami w tym repozytorium. GitHub Issues w prywatnym
repozytorium `lakomytomaszdev/plasma-tide` pozostają dostępne dla innych zgłoszeń (CLI `gh`), ale nie są
źródłem ticketów przepływu karty ani stanów jego planu.

| Rodzaj | Miejsce |
| --- | --- |
| Specyfikacja | `docs/agents/specs/YYYY-MM-DD-<slug>.md` |
| Plan i stan ticketów | `docs/agents/plans/YYYY-MM-DD-<slug>.md` |
| Kartki ticketów | `docs/agents/tickets/<data>-<slug>/<kolejność>-<id>-<slug>.md` |
| Wynik ticketu | `docs/agents/tickets/<data>-<slug>/results/<id>.md` |

Plan jest źródłem prawdy o stanie. Aktualizuj tabelę i `updated` przy delegowaniu, wyniku, review oraz pytaniu
użytkownika. Zasady formatu, ról i przepływu karty opisuje [standard planu](../agent-plan-standard.md).

Numeracja lokalna jest ciągła w całym repozytorium. Przed nowym numerem uruchom `make check-plans`, a numer
zarezerwuj przez `make next-id REPO=/home/user/DevWorkspace/plasma-tide TITLE="…"`; nie obliczaj go ręcznie.
Ticket w YouTracku zachowuje klucz nadany przez YouTrack, jeśli projekt na tablicy ma taki tracker.

Gdy instrukcja narzędzia mówi „opublikuj w trackerze” dla przepływu karty, oznacza to zapis właściwego pliku,
aktualizację planu i commit na gałęzi pracy. Gdy mówi „pobierz ticket”, przeczytaj kartkę z katalogu
`docs/agents/tickets/` oraz powiązany wiersz planu. Nie zakładaj GitHub Issue dla tej samej pracy.
