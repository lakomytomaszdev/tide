# Jawne decyzje backlog/go

Te polecenia przekazują wyłącznie gotową sprawę do gotowego Tide CLI. Nie syntezuj
raportu ponownie, nie planuj pracy, nie twórz karty samodzielnie i nie wybieraj
wykonawców. Nie odwołuj się do prywatnego API, Tablicy ani qdbus. Helper zapisuje
stan operacji w katalogu sesji pod `${XDG_STATE_HOME:-$HOME/.local/state}/tide/issue-decisions/<id-rozmowy>/S-N`.
Każda rozmowa ma własny trwały identyfikator; zachowaj go i ten sam katalog przy wznowieniu.

## Backlog i go

Rozpoznaj wyłącznie `backlog S-N vN` lub `go S-N vN`, opcjonalnie z jawnym plikiem
ustawień go. Numer wersji, destination i ustawienia są częścią decyzji. Nie
przypisuj polecenia „backlog S-N” albo „go S-N” do najnowszej wersji odczytanej
samodzielnie.

Jeśli użytkownik nie podał konkretnej wersji i zgody dotyczącej destination oraz
ustawień, najpierw pokaż sprawę przez helper. `inspect` pobiera Show i wyświetla
cały raport wraz z S-N, version, stanem, tytułem, projektem, manifestem dowodów,
destination oraz ustawieniami go (albo jawnie podaje, że zastosowane będą domyślne
ustawienia CLI). Następnie zakończ bez mutacji i poczekaj na odrębną odpowiedź.
Napisz użytkownikowi dokładną frazę potwierdzenia zwróconą przez helper, np.
`Potwierdzam go S-12 v4`. Nie traktuj samoistnie świeżo odczytanej wersji jako zgody.

Użyj trwałego katalogu sesji, nie nowego katalogu przy wznowieniu. Przykładowo:

```text
python3 <adapter> inspect --session <stan>/S-12 --id S-12 --destination go
python3 <adapter> inspect --session <stan>/S-12 --id S-12 --destination go --launch <ustawienia.json>
python3 <adapter> backlog --session <stan>/S-12 --id S-12 --expected-version 4 --decision "backlog S-12 v4"
python3 <adapter> go --session <stan>/S-12 --id S-12 --expected-version 4 --decision "go S-12 v4"
python3 <adapter> go --session <stan>/S-12 --id S-12 --expected-version 4 --decision "Potwierdzam go S-12 v4" --launch <ustawienia.json>
```

Wpisuj ścieżkę helpera zgodnie z programem: Claude `${CLAUDE_PLUGIN_ROOT}/scripts/issue_adapter.py`;
Codex `${XDG_DATA_HOME:-$HOME/.local/share}/tide/agents/codex/tide-issue/scripts/issue_adapter.py`.
W poleceniach przekazuj tekst użytkownika i ścieżki jako osobne argumenty. Nie interpoluj
ich do kodu powłoki, nie konstruuj własnego JSON-u dla D-Bus. Plik launch ma być JSON-em
ustawień istniejącego CLI; jego dokładna treść zostaje skopiowana do lokalnego zapisu.

Helper zapisuje przed mutacją S-N, accepted expectedVersion, destination, dokładny launch
i request-id. Jeżeli odpowiedź zaginie, uruchom `replay` dla backlog/go albo
`retry-replay` dla retry, w tym samym katalogu, z `--request-id <zapisany requestId>`.
Zachowaj requestId operacji w rozmowie przed wywołaniem i wybieraj nim również starszą operację.
Jeśli katalog ma kilka operacji, helper odmawia replay bez wskazania klucza; nie wybieraj
samodzielnie ostatniej operacji innej rozmowy. Nie generuj nowego klucza. Replay przekazuje
ponownie identyczny ładunek do CLI. Gdy brak lokalnej tożsamości operacji, nie zgaduj:
odczytaj i pokaż raport, po czym uzyskaj nową, odrębną decyzję.

Version-conflict, draft, rejected, nieznane S-N i każda odmowa kończą się bez automatycznej
zmiany wersji, submit, reopen, akceptacji nowszej wersji ani kolejnej mutacji. Po konflikcie
pokaż bieżący raport oddzielnie i poczekaj na decyzję dotyczącą tej wersji. Konflikt musi
pozostać widoczny; nie obchodź go odświeżeniem i automatycznym ponowieniem.

Backlog wykonuje jedną akceptację i zwraca K-N. Go przekazuje dokładnie zgodę oraz launch
do `tide go`; bez launch pozostawia wybór jawnie udokumentowanym domyślnym ustawieniom CLI.
Pokaż rezultat zapisanej operacji oddzielnie od `currentStart` zwróconego przez Show.
Uwzględnij accepted, K-N, queued/started/startError i błędy. Pierwotne go ponawia się
wyłącznie przez replay tego samego request-id; nawet naprawa przyczyny startError nie jest
zgodą na ponowne uruchomienie.

## Świadome ponowienie startu

Po osobnej prośbie użytkownika najpierw uruchom `retry-inspect` i pokaż bieżące intentId,
startVersion, retryable, stan oraz K-N. Nie używaj historycznego wyniku go jako bieżącego
stanu. Tylko gdy Show podaje `retryable: true`, zapytaj o odrębną decyzję dokładnie dla
pokazanych intentId/startVersion. Po potwierdzeniu wywołaj `retry-start`; helper utworzy
nowy klucz retry i zachowa go przed wywołaniem CLI. Przy utracie odpowiedzi użyj wyłącznie
`retry-replay`. Retry nie akceptuje sprawy ponownie i nie zmienia launch. Konflikt lub
odmowa nie są obchodzone świeżym odczytem; nowa próba wymaga kolejnego odrębnego odczytu
i decyzji użytkownika.
