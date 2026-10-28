# Tide — słownik

Pojęcia, którymi mówią kod, dokumentacja, plany i agenci. Uzasadnienia decyzji: `docs/decisions.md`.
Gdy potrzebujesz pojęcia, którego tu nie ma, dopisz je zamiast wymyślać synonim.

## Tablica i karty

- **Karta** — rzecz do zrobienia na Tablicy (funkcja, poprawka, pomysł), `K-<numer>`. Nie ticket: jedna karta
  ma zwykle kilka ticketów. _Unikaj:_ zadanie (w UI „Nowe zadanie” to osobne okno), issue.
- **Kolumna** — Backlog, Do zrobienia, W toku, Czeka na mnie, Do sprawdzenia, Gotowe (i ukryte Archiwum).
  Ręcznie przestawiasz tylko Backlog, Do zrobienia i Gotowe; pozostałe wynikają ze stanu karty.
- **Znacznik karty** — krótki opis stanu na karcie („planowanie · Opus”, „plan do zatwierdzenia”, „fala 2 z 3”,
  „pytanie z PT-51”). Stany przepływu są znacznikami, nie kolumnami.
- **Zlecenie** — start pracy nad kartą z okna „Co ma zrobić agent?”; zawsze przez jedno z **wejść**.
- **Wejście** — sposób rozpoczęcia zlecenia: *Planowanie*, *Mam plan*, *Od razu*, *Tylko specyfikacja*.

## Przepływ karty

- **Przepływ karty** — praca nad kartą prowadzona przez Tide (usługę), a nie przez długą sesję AI. Sesje AI to
  krótkie kroki uruchamiane przez Tide. _Unikaj:_ „koordynator prowadzi kartę” (dawny model).
- **Krok** — jedna sesja AI z jednym zadaniem i małym wejściem: *planowanie*, *uzupełnij plan*, *popraw plan*,
  *ticket*, *review*, *poprawki*, *konflikt*, *domknięcie*. Krok kończy się wynikiem: gotowe, pytanie, brak zgody,
  błąd, limit albo zatrzymany. _Unikaj:_ podagent (to mechanizm długiej sesji, nie kroku).
- **Krok w tle** — krok bez okna (`claude -p`, `codex exec`); terminal otwiera dopiero „Wejdź”.
- **Planujący** — wykonawca kroku planowania. Proponuje plan, nie zleca pracy. _Unikaj:_ koordynator
  (w nowym przepływie koordynuje Tide).
- **Plan do zatwierdzenia** — propozycja planu po kroku planowania (albo po „Mam plan”); karta czeka w „Czeka na mnie”,
  aż użytkownik zatwierdzi, odeśle z uwagami albo wejdzie w rozmowę.
- **Fala** — grupa ticketów, które mogą iść równolegle; Tide wylicza fale z zależności (`Po`), nie zapisuje ich.
- **Gałąź karty** — gałąź i worktree karty (`feat/k-32-…`); tickety odgałęziają się od niej i są do niej scalane
  po review z APPROVE. Do gałęzi głównej scala tylko użytkownik.
- **Domknięcie** — ostatni krok karty: podsumowanie, lista „Co przetestować ręcznie i jak” (przy `check: manual`),
  karta do „Do sprawdzenia”.
- **Dziennik zdarzeń** (dziennik karty) — `runs/K-<n>/events.jsonl` w repozytorium tablicy, dopisywany linia po linii;
  jedyne źródło stanu karty w przepływie karty. Plan, kolumny, raport i podgląd to widoki dziennika.
- **Zdarzenie** — jedna linia dziennika (`step-started`, `plan-approved`, `review-verdict`…).

## Tickety i przekazanie

- **Ticket** — jednostka pracy w planie karty, `PREFIKS-numer` (np. `PT-44`, `LOG-240`). Przed zatwierdzeniem
  ma numer roboczy `#1`, `#2`; numer albo zgłoszenie w YouTracku dostaje przy zatwierdzeniu planu.
- **Kartka przekazania** (kartka) — krótki plik wejściowy kroku ticketu: cel, kryteria akceptacji, fragment
  specyfikacji, miejsca w kodzie, czego nie robić, sekcja „Dla recenzenta”. Pisze ją planujący. _Unikaj:_ handoff
  w UI (w kodzie `brief.md`).
- **Notka wyniku** — co zrobił krok ticketu (`result.md`); trafia do kartek zależnych ticketów, do recenzenta
  i jako komentarz do zgłoszenia w YouTracku.
- **Przekazanie techniczne** — zrobione przez Tide bez modelu przy zatrzymaniu kroku (limit, „Przekaż innemu
  dostawcy”): commit WIP na gałęzi ticketu i notka z listą zmian i ostatnimi wiadomościami agenta.

## Modele i wykonawcy

- **Program** — Claude Code albo Codex CLI; to, co uruchamia Tide.
- **Punkt** — serwer modeli: chmura dostawcy, `local` (Ollama na tym komputerze), przyszły `mac`, inny punkt zgodny
  z OpenAI albo Anthropic.
- **Dostawca** — kto dostarcza model: Anthropic, OpenAI, lokalny, inny (np. przez punkt zgodny z OpenAI).
- **Wykonawca** — program + model + punkt pod jedną nazwą („Sonnet”, „Codex”, „Lokalny · qwen3-coder:30b”);
  katalog wykonawców jest w widoku Modele. W planie: kolumna `Agent`. _Unikaj:_ implementer jako nazwa wykonawcy.
- **Klasa** — *lekki*, *standard*, *mocny*, *lokalny*; zasady przydziału mówią klasami, a katalog zamienia klasę
  na wykonawcę preferowanego dostawcy.
- **Preferowany dostawca** — wybór przy zleceniu: Anthropic, OpenAI, mieszany, tylko lokalnie.
- **Zasady przydziału** — krótki tekst na tablicy (wspólny, z nadpisaniem per projekt), który planujący dostaje
  razem z katalogiem wykonawców.
- **Lista dozwolonych poleceń** — per projekt na tablicy; co krok w tle może uruchomić bez pytania.
