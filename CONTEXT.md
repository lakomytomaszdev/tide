# Tide

Własne środowisko pracy z agentami AI na KDE Plasma: Tablica kart, sesje agentów, modele (chmura i lokalne)
i raporty. Słownik obowiązuje w kodzie, dokumentacji, planach i poleceniach dla agentów; uzasadnienia decyzji są
w `docs/decisions.md`.

## Language

### Tablica

**Repozytorium tablicy**:
Repozytorium gita z kartami, projektami, szablonami zleceń i dziennikami kart; commituje je wyłącznie Tide.
_Avoid_: tide-board w tekstach dla użytkownika

**Tablica**:
Jedno miejsce z rzeczami do zrobienia we wszystkich projektach, ułożonymi w kolumny.
_Avoid_: board w tekstach dla użytkownika, kanban

**Karta**:
Rzecz do zrobienia na Tablicy (funkcja, poprawka, pomysł); zwykle obejmuje kilka ticketów.
_Avoid_: zadanie, issue, task

**Kolumna**:
Miejsce karty na Tablicy: Backlog, Do zrobienia, W toku, Czeka na mnie, Do sprawdzenia, Gotowe i ukryte Archiwum.
Ręcznie przestawia się Backlog, Do zrobienia, Gotowe i Archiwum; resztę wyznacza stan karty.
_Avoid_: status karty

**Znacznik karty**:
Krótki opis stanu karty w jej kolumnie, np. „plan do zatwierdzenia” albo „fala 2 z 3”.
_Avoid_: podkolumna, faza

**Panel karty**:
Widok szczegółów karty w oknie Tide: plan do zatwierdzenia, przebieg, pytania, sprawdzenie.
_Avoid_: szczegóły karty, sidebar

**Zlecenie**:
Rozpoczęcie pracy nad kartą przez użytkownika, zawsze przez jedno wejście.
_Avoid_: uruchomienie agenta, launch

**Wejście**:
Sposób rozpoczęcia zlecenia: Planowanie, Mam plan, Od razu albo Tylko specyfikacja.
_Avoid_: tryb (tryb to Pełny albo Lekki)

**Tryb**:
Głębokość planowania: Pełny (rozmowa z użytkownikiem) albo Lekki (bez rozmowy).
_Avoid_: wejście

### Przepływ karty

**Przepływ karty**:
Praca nad kartą prowadzona przez Tide, w której sesje AI są krótkimi krokami.
_Avoid_: koordynacja (to dawny model z długą sesją koordynatora)

**Krok**:
Jedna krótka sesja AI z jednym zadaniem w przepływie karty: planowanie, uzupełnienie planu, poprawa planu, review
planu, ticket, review, poprawki, konflikt albo domknięcie.
_Avoid_: podagent, run, job

**Krok w tle**:
Krok bez okna terminala; użytkownik wchodzi do niego tylko na żądanie.
_Avoid_: sesja headless, zadanie w tle (to zadania modeli, np. raport)

**Wynik kroku**:
Jak krok się skończył: gotowe, pytanie, brak zgody, błąd, limit albo zatrzymany.
_Avoid_: status kroku

**Planujący**:
Wykonawca kroku planowania; proponuje plan, ale niczego nie zleca.
_Avoid_: koordynator

**Koordynator**:
Długa sesja AI, która sama dzieli pracę i zleca ją podagentom — dawny model, zostaje dla kart uruchomionych przed
przepływem karty.
_Avoid_: planujący

**Plan do zatwierdzenia**:
Propozycja planu karty czekająca na decyzję użytkownika: zatwierdzić, odesłać z uwagami albo wejść w rozmowę.
_Avoid_: szkic planu

**Propozycja planu**:
Plan zapisany przez planującego (albo przejęty), zanim użytkownik go zatwierdzi; tickety mają numery robocze.
_Avoid_: szkic planu

**Sprawdzanie formatu**:
Kontrola propozycji planu przez Tide bez modelu: znani wykonawcy, blokery, brak cykli, kartki z kryteriami.
_Avoid_: walidacja w tekstach dla użytkownika, review

**Review planu**:
Sprawdzenie propozycji planu przez innego wykonawcę niż planujący, zanim zobaczy ją użytkownik.
_Avoid_: sprawdzanie formatu

**Przejęcie planu**:
Powiązanie istniejącego planu (np. z przekazania z innej sesji) z kartą przez wejście Mam plan.
_Avoid_: import, handoff

**Ticket z innego planu**:
Ticket w stanie `todo` z innego planu tego samego repozytorium, który propozycja planu przyjmuje pod tym samym tytułem z zachowanym numerem i polem `fromPlan`; inny tytuł albo stan inny niż `todo` jest odrzucony z komunikatem.
_Avoid_: duplikat, import ticketu

**Fala**:
Poziom w grafie blokerów karty: tickety, które dzieli od początku tyle samo blokerów; mogą iść równolegle.
_Avoid_: etap, sprint

**Bloker**:
Ticket, który musi być gotowy, zanim zacznie się inny ticket.
_Avoid_: zależność w tekstach dla użytkownika

**Gałąź karty**:
Gałąź, do której trafiają tickety karty po review; do gałęzi głównej przenosi ją tylko użytkownik.
_Avoid_: gałąź koordynatora, gałąź integracyjna

**Domknięcie**:
Ostatni krok karty: podsumowanie pracy i lista rzeczy do sprawdzenia przez użytkownika.
_Avoid_: finalizacja, merge

**Dziennik karty**:
Uporządkowany zapis wszystkich zdarzeń karty; jedyne źródło jej stanu w przepływie karty.
_Avoid_: log, historia, plan (plan to widok dziennika)

**Zdarzenie**:
Jedna zapisana zmiana w dzienniku karty, np. start kroku, zatwierdzenie planu, werdykt review.
_Avoid_: wpis, event w tekstach dla użytkownika

### Tickety

**Ticket**:
Jednostka pracy w planie karty z własnym ID (np. `PT-58`, `LOG-240`); przed zatwierdzeniem ma numer roboczy (`#1`).
_Avoid_: zadanie, issue, karta

**Zgłoszenie**:
Ticket albo karta w zewnętrznym trackerze (YouTrack), np. `LOG-240`.
_Avoid_: issue, task

**Kartka przekazania**:
Krótkie wejście kroku ticketu: co zbudować, kryteria akceptacji, blokery, potrzebny fragment specyfikacji i część
„Dla recenzenta”.
_Avoid_: handoff, prompt, kartka (bez dopowiedzenia, gdy mowa o karcie)

**Notka wyniku**:
Opis tego, co zrobił krok ticketu; trafia do recenzenta, do ticketów blokowanych i do zgłoszenia w trackerze.
_Avoid_: raport, summary

**Przekazanie techniczne**:
Stan zatrzymanego kroku zapisany przez Tide bez udziału modelu (zmiany w kodzie i ostatnie wiadomości agenta), żeby
krok mógł dokończyć inny wykonawca.
_Avoid_: handoff (bez dopowiedzenia)

### Modele i wykonawcy

**Program**:
Narzędzie agenta, które uruchamia Tide: Claude Code albo Codex CLI.
_Avoid_: agent, narzędzie

**Punkt**:
Serwer modeli, z którym rozmawia program: chmura dostawcy, ten komputer, Mac albo inny serwer zgodny z API OpenAI
lub Anthropic.
_Avoid_: endpoint w tekstach dla użytkownika

**Dostawca**:
Kto dostarcza model: Anthropic, OpenAI, lokalny albo inny.
_Avoid_: provider w tekstach dla użytkownika

**Wykonawca**:
Program z modelem i punktem pod jedną nazwą z katalogu, np. „Sonnet”, „Codex”, „Lokalny · qwen3-coder:30b”.
Ten sam wykonawca może pełnić różne role.
_Avoid_: agent, implementer, model (model to część wykonawcy)

**Autor ticketu**:
Rola wykonawcy, który robi ticket (krok ticketu i poprawki).
_Avoid_: wykonawca ticketu, implementer

**Recenzent**:
Rola wykonawcy, który robi review ticketu albo planu; zawsze inny model niż autor.
_Avoid_: reviewer w tekstach dla użytkownika

**Klasa**:
Siła wykonawcy potrzebna do ticketu: lekki, standard, mocny albo lokalny; zamieniana na wykonawcę wybranego dostawcy.
_Avoid_: poziom, tier

**Preferowany dostawca**:
Wybór przy zleceniu: Anthropic, OpenAI, mieszany albo tylko lokalnie.
_Avoid_: domyślny model

**Zasady przydziału**:
Krótki tekst, który mówi planującemu, jakiej klasy wykonawca pasuje do jakiej pracy.
_Avoid_: reguły routingu

**Lista dozwolonych poleceń**:
Polecenia, które krok w tle może uruchomić w projekcie bez pytania użytkownika.
_Avoid_: allowlist w tekstach dla użytkownika
