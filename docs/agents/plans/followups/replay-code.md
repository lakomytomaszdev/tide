# K-50-replay-code — korekta bazy z istniejącą pracą

Status: zapisany szkic zakresu następczego, poza v1 [planu K-50](../2026-11-05-k-50-flow.md). Stały klucz przekazania: K-50-replay-code. Bez nadanego numeru K/PT, bez publikacji na Tablicy, bez przydziału i bez zgody na realizację. Tide może utworzyć kartę dopiero po osobnej decyzji użytkownika.

## Problem i granica przekazania

K-49 po wykonanych krokach wymaga przeniesienia istniejącej pracy z niewłaściwej bazy. Pusta korekta v1 K-50 tego nie rozwiązuje. Każdy commit dokumentów planu, kodu, ticketu lub WIP jest pracą: v1 pokazuje odmowę, zachowuje dane i odsyła tutaj. Najnowsza decyzja użytkownika odkłada również liniowe dokumenty planu Tide; nie ma ich replay w ticketach v1.

## Zakres do osobnego zaplanowania

- Dowiedzione dziennikiem granice wkładu i własność dokumentów; liniowe przeniesienie dokumentów planu i mapowanie pustych ticketów jako możliwy pierwszy wycinek, następnie kod i WIP. Nie wystarczy nazwa pliku, data lub treść komunikatu commita.
- Zachowanie oryginalnych refów/SHA i worktrees, także plików ignorowanych; przygotowanie poza aktywną generacją, mapa stary SHA → nowy SHA bez wciągania historii błędnego develop.
- Odtwarzanie grafu scalonych i niescalonych/równoległych ticketów według zapisanych punktów odgałęzienia, bez dublowania wkładu; znane merge i ich rozstrzygnięcia. Unknown, obce merge i nieudowodniony zakres pozostają odmową.
- Potwierdzony stop wszystkich procesów, WIP przez istniejącą ścieżkę, świeży podgląd i ponowna zgoda po zmianie zakresu. Brak pewnego stop blokuje przełączenie.
- Checkpointy i recovery wieloetapowego replay na mechanizmie pustej generacji, pojedyncza aktywacja całej mapy, nowa sesja CLI i przekazanie; brak połowicznej migracji po awarii.
- Unieważnienie starych review/domknięcia/tokenów, ponowne review/testy także wcześniej scalonych ticketów. Rozwiązywanie konfliktów wyłącznie w przygotowywanej generacji po osobnej decyzji; anulowanie zachowuje starą generację.

## Oczekiwane dowody przyszłej pracy

Publiczny /Board, rzeczywiste repozytoria: dokumenty po Lekkie/Mam plan/istniejącym Pełnym, kod karty i ticketów, WIP po stop, dwa równoległe tickety, merge, restart w checkpointach, konflikt i odmowy. Stare SHA pozostają osiągalne, delta nie zawiera obcej historii, identyfikatory/plan/blokery pozostają, a nowe review odnosi się do nowych SHA. Ten szkic nie udziela APPROVE żadnemu przyszłemu planowi.

Do dostarczenia takiej funkcji: zachować bazę, żeby kontynuować, albo pozostawić kartę w oczekiwaniu; nie resetować/rebase'ować zarządzanych refów ani usuwać worktrees. Ewentualne nowe zlecenie na prawidłowej bazie jest osobną decyzją i nie oznacza automatycznego przeniesienia zachowanej pracy.
