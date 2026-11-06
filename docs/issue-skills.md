# Sprawa Tide z rozmowy

Paczka zawiera jedno źródło syntezy (`cli/agents/shared/synthesis.md`), wspólny helper
operacji CLI oraz dwa wejścia: plugin Claude Code `tide` i skill Codexa `tide-issue`.
Raport opisuje problem i oczekiwanie, a przyjęcie sprawy pozostaje decyzją użytkownika.
Nie uruchamia implementacji ani nie tworzy karty.

## Instalacja i wywołania

Instalacja komponentu CLI obejmuje paczkę agentów. W trakcie testów uruchamiamy ją
wyłącznie w izolowanym HOME/XDG. Standardowa instalacja projektu ma własną kopię
zapasową przed zmianą pulpitu; instalator paczki dodatkowo chroni swoje pliki manifestem
SHA-256 i robi kopię przy aktualizacji/odinstalowaniu. Kolizja z obcym albo zmienionym
plikiem przerywa operację bez nadpisania. Obce pliki w katalogu skilla pozostają.
Nie kopiuje metodologii, ustawień, hooków ani konfiguracji MCP użytkownika.

Plugin jest zainstalowany w `${XDG_DATA_HOME:-$HOME/.local/share}/tide/agents/claude/tide`.
Uruchom Claude Code z tą ścieżką, następnie w rozmowie wywołaj `/tide:issue`:

```sh
claude --plugin-dir "${XDG_DATA_HOME:-$HOME/.local/share}/tide/agents/claude/tide"
```

Oba adaptery są zawsze w paczce w `tide/agents` pod XDG_DATA_HOME, nawet bez programów.
Skill Codexa trafia dodatkowo do `~/.codex/skills/tide-issue`, gdy program jest dostępny przy
instalacji. W nowej rozmowie Codexa wywołaj `$tide-issue`. Starsze rozmowy mogą wymagać
ponownego uruchomienia programu, by wykryć nową instalację. Składnia `/tide:issue` jest
poleceniem pluginu Claude Code, a nie składnią obsługiwaną przez Codexa.
Brak jednego programu nie blokuje instalacji drugiego; po doinstalowaniu programu
ponów instalację paczki. Plugin Claude nie wymaga rejestracji w prywatnej konfiguracji
ani publikacji marketplace. Odinstalowanie komponentu CLI usuwa wyłącznie pliki Tide
objęte manifestem i pozostawia kopie zapasowe oraz pliki obce.

## Trwały szkic i poprawki

Adapter najpierw sprawdza API, zapisuje początkowy raport przez CLI i pokazuje S-N/draft.
Dopiero wtedy następuje dłuższa synteza. Katalog sesji helpera jest prywatnym stanem
rozmowy (request-id, pierwotny ładunek i oczekiwana wersja), nie repozytorium Tablicy.
Zachowaj go przy wznowieniu i ponawianiu utraconej odpowiedzi create. Nie zmieniaj
request-id, aby ominąć błąd. Do poprawki istniejącej sprawy służy resume z S-N i odczytem
wersji. Konflikt zatrzymuje operację; decyzja o jego rozstrzygnięciu należy do użytkownika.

Helper ma operacje begin/resume/finish/waive opisane we wspólnych instrukcjach.
Wszystkie zapisy sprawy przechodzą przez `tide issue ... --json` z jawnymi metadanymi.
Po błędzie open zapisany numer nadal istnieje; można ponowić samo `tide issue open S-N`.
Raport nie zawiera frontmatteru zarządzanego przez usługę. Brak bajtów obrazu lub błąd
attach pozostawia opisany draft. Usługa waliduje manifest, nie listę braków wyczytaną
z prozy. Odrębna jawna rezygnacja z brakującego dowodu aktualizuje ten sam raport,
zachowuje ograniczenie dowodów i pozwala submitować nową wersję bez usuwania załączników.

## Trzy osobne dowody

1. Testy statyczne sprawdzają zainstalowane wejścia i wspólną instrukcję. Nie oceniają tekstu modelu.
2. `python3 -m unittest daemon.tests.test_issues_adapters -v` sprawdza instalację i
   adapter → rzeczywisty CLI → prywatny daemon, wersje, kopie dowodów, brak submit przy
   niedostępnym obrazie, odrębną rezygnację, konflikty, utratę odpowiedzi i przerwanie procesu.
   Obserwator CLI nie zastępuje operacji atrapami; izolowany launcher okna zapisuje wybór.
3. `cli/agents/evaluate.py` jest osobną, sieciową oceną rzeczywistej syntezy obu programów.
   Nie należy do `make test`. Wymaga jawnego uruchomienia poza piaskownicą kroku,
   może zużywać płatny limit. Tworzy izolowaną Tablicę, HOME/XDG, prywatny D-Bus i
   używa rzeczywistych programów. Czyta wyłącznie pliki uwierzytelnienia, jeżeli są
   dostępne; nie importuje prywatnych ustawień, hooków ani MCP.

Kontrolowane wejście: `cli/agents/eval/conversation.txt`, `opis.txt` i deterministyczny
obraz generowany przez `fixture.py` jako `filtr.png`. Drugi obraz nie ma pliku ani
żadnych bajtów. Ten sam zestaw dostają oba programy. Protokół oceny izoluje etap
syntezy: helper zapisuje draft przed modelem, model ogląda obraz i tworzy treść
raportu, helper dołącza pliki i zachowuje draft z brakiem. To nie jest test automatycznego
doboru skilla przez program. Osobno sprawdzamy instalowane wejścia i operacje helpera.

Przykład świadomego uruchomienia (modele odpowiadają katalogowi wykonawców projektu):

```sh
python3 cli/agents/evaluate.py --output build/issue-synthesis/pt-224 --claude-model sonnet --codex-model gpt-6.1-sol
```

`bash scripts/evaluate-issue-synthesis.sh` najpierw wykonuje deterministyczny moduł
testów adapterów na prywatnym D-Bus. Dopiero po sukcesie uruchamia powyższą sieciową
ocenę. Te dwa wyniki pozostają odrębnymi dowodami; skrypt nie ocenia sam jakości raportów.

Katalog wyniku jest nowy, by nie nadpisywać wcześniejszej oceny. Zawiera wersje programów,
żądane i odczytane z transkryptów modele, wejście, raporty, transkrypty, wyniki CLI,
sprawdzone odczytem CLI kopie załączników i niewypełnioną rubrykę dla każdego programu.
Po przebiegu oceń wszystkie siedem pozycji `rubric.json` na podstawie raportu oraz
wyników CLI: pass/fail z dokładnym cytatem lub wskazaniem fragmentu. Zapisz werdykt
pass tylko przy siedmiu pass; utrata warunku, odrzuconego wariantu lub fikcyjny dowód
oznacza fail. Nagłówki ani asercje argumentów procesu nie zastępują tej oceny.
Nie deklaruj spełnienia kryterium syntezy bez obu rzeczywistych raportów i rubryk.

Odrębny przebieg po świadomej rezygnacji użytkownika może wykonać waive na zachowanej
sesji i tym samym S-N, z dosłowną odpowiedzią „rezygnuję z drugiego obrazu”. Pierwszy
przebieg nie otrzymuje tej decyzji. Deterministyczny test sprawdza tę sekwencję oddzielnie.
