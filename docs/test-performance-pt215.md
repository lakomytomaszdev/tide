# PT-215 — pomiar pełnych testów

Pomiar z 5 listopada 2026 r., rozpoczęty o 19:27:55 +01:00, poza piaskownicą,
w worktree ticketu na commicie `e118500081023bb7c7efa3e78fb9cce63e19207d`.
Polecenie: `make test-benchmark`. Maszyna udostępnia 16 rdzeni; limit równoległości: 14.

| Przebieg | Czas | Testy | Pominięte |
| --- | ---: | ---: | ---: |
| Dawny `discover`, całe katalogi kolejno | 2447,59 s (40 min 47,59 s) | 4163 | 2 |
| Równoległy `make test JOBS=14` | 368,98 s (6 min 8,98 s) | 4163 | 2 |

Przyspieszenie: **6,63×**. 236 modułów, 27 katalogów; identyfikatory testów wraz
z krotnością powtórzeń są identyczne. Nie zgubiono ani nie dodano wykonanych testów
między tymi dwoma sposobami uruchomienia. Wynik obejmuje 12 nowych testów runnera.

Źródło: `build/test-benchmark/comparison-q4q0ub19/comparison.json`, `report.md`,
logi katalogów oraz `parallel.log`. Pierwotny pełny log polecenia znajduje się również
w katalogu kroku `pt-215-szybkie-testy-rownolegly-make.step/outside/outside.log`.

## Błędy już obecne w przebiegu sekwencyjnym

Oba przebiegi zgłosiły te same trzy awarie:

- `test_board_derive.LiveTest.test_agents_and_coordination_changes_move_card_live`:
  po utworzeniu karty kolumna `review` zamiast `todo`.
- `test_card_branches.CardBranchesTest.test_no_main_deleted_ref_stale_sha_and_branch_collision`:
  komunikat ogólny zamiast oczekiwanego zawierającego „tylko”.
- `test_cardflow_base_change.FollowupArtifactTest.test_approved_sketch_scope_links_and_installation_copy`:
  plan K-50 nie zawiera wymaganego odnośnika `followups/replay-code.md`.

Wszystkie trzy odtworzono też pojedynczo w bieżącym worktree przez `python3 -m unittest`
(3 testy, 0,605 s, 3 porażki). Nie zmieniamy tych testów ani zachowania innych kart w PT-215.
Brak odnośnika w istniejącym planie nie jest dowodem nieprawidłowej bazy.

## Różnice w pierwszym pomiarze

Sekwencyjnie dodatkowo nie przeszedł
`test_cardflow_dbus.JournalTest.test_journal_removed_from_outside_comes_back_with_a_warning`:
odtworzono dziennik, lecz odczytana lista błędów nie miała jeszcze ostrzeżenia.

Równolegle dodatkowo nie przeszły:

- `test_cardflow_review.ReviewFlowTest.test_reject_after_three_rounds_records_decision_without_merging`:
  liczba `step.started` wzrosła z 6 do 7.
- `test_cardflow_youtrack.YouTrackFlowTest.test_offline_result_comment_does_not_block_next_ticket`:
  uruchomione `LOG-1`, `LOG-3` zamiast `LOG-1`, `LOG-2`.

Nie ma różnic w pominięciach ani oczekiwanych porażkach. Pełny benchmark poprawnie zwrócił
kod niezerowy i końcową listę pięciu awarii równoległego przebiegu. Stabilność trzech
różniących się przypadków jest nadal sprawdzana osobno i pod obciążeniem; powyższy pomiar
potwierdza wydajność i kompletność, a nie bezbłędność całego zestawu.

## Zawężona próba i izolacja

Próba `build/pt215/probe.py`, wykonana poza piaskownicą, zapisała wyniki w
`build/pt215/probe-3xyoh3bt/`. Trzy przypadki wykonane po 3 razy pojedynczo przeszły
wszystkie 9 prób. Przy 14 jednoczesnych procesach odrzucenie review miało 1/5 porażek,
odczyt ostrzeżenia D-Bus 2/4, a komentarz YouTracka przeszedł 5/5 prób.

Zapisany dziennik potwierdził, że siódmym krokiem po odrzuceniu review jest domknięcie
karty. Test liczy kroki, zanim kolejny tick zdąży je uruchomić. Test ostrzeżenia odczytuje
migawkę zaraz po pojawieniu się odtworzonego pliku — publikacja ostrzeżenia jest osobna.
Oba moduły oznaczono do wykonania pojedynczo **po zakończeniu części równoległej**;
`tools/test_suites.py` zawiera uzasadnienia i wyniki próby. Nie zmieniono ich asercji.

Fixture `FlowInProcess` używała produkcyjnego `stop()`, które zamyka pulę z `wait=False`.
Stary wątek mógł nadal zmieniać dziennik podczas restartu silnika, cofania środowiska testu
lub usuwania repozytorium. W wariancie monitora używanym wyłącznie przez fixture dodano
oczekiwanie na zakończenie puli i dostarczenie callbacków Qt przed powrotem ze `stop()`.
Nowy test `test_cardflow_fixture` wykazał błąd przed poprawką i przeszedł po niej.
Nie zmieniono produkcyjnego monitora ani oczekiwanego działania przepływu.

Kontrola lokalna: `python3 -m unittest daemon.tests.test_cardflow_fixture scripts.tests.test_test_runner`
— 13 testów, 0,975 s, OK. Po dodaniu regresji pełny zestaw powinien mieć 4164 testy
w 237 modułach. Końcowy pełny przebieg po zmianach izolacji pozostaje do wykonania;
nie ma potrzeby powtarzania pomiaru sekwencyjnego.
