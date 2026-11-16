---
status: accepted
date: 2026-11-16
source: przegląd architektury 15–16.11.2026 na prośbę właściciela; decyzja właściciela 16.11.2026 (docs/agents/masterplan/CURRENT.md, Historia zmian „16.11.2026 ~14:30 — masterplan v1.1 po ADR-7”), analiza koordynatora build/coordinator-notes/2026-11-16-adr7-analiza.md, konsultacja Astry
---

# ADR-7: Zdarzenia, indeks i doręczanie — kierunek po v1

Kierunek dla v1.1 i v2. **Nie zmienia zakresu v1** ani żadnej karty v1 (w tym planu K-109); nic z tego ADR nie startuje
przed M5.

## Kontekst

Dziennik karty jest logiem zdarzeń, a stan karty liczy z niego `fold` (decyzja 84 w `docs/decisions.md`). Rdzeń jest
więc zdarzeniowy. Wokół niego brakuje trzech rzeczy:

1. **Spisu zdarzeń.** 69 dzienników ma 66 011 zdarzeń w 79 typach, wszystkie z `v=1` (odczyt 16.11 15:55). Typy są
   napisami rozsianymi po modułach, bez opisu pól i bez wersji typu.
2. **Doręczania.** 11 z 14 sygnałów D-Bus usługi nie niesie treści (`dbus_service.py`), więc odbiorca po sygnale pobiera
   cały stan. Silnik przegląda karty co 5 s (`cardflow_monitor.POLL_MS`, `cardflow_monitor.py:33`), a koniec kroku
   wykrywa poleceniem `systemctl is-active` (`tasks/stepstop.unit_state`). K-105, K-113 i K-125 kolejno obniżyły koszt
   postoju: 98,7% → 70,75% → 62,7% → 19,8% rdzenia w produkcji przy progu M0 30%. Pomiar po K-125 zakończony 16.11
   w nocy, PASS w obu trybach: pudełko wyłączone — okna 18,5–22,2% (średnia 19,8%), Snapshot p95 224 ms; pudełko
   włączone — okna 16,1–21,1% (średnia 17,4%), p95 342 ms. **Przegląd co 5 s nie jest już zmierzonym kosztem postoju:**
   `engine.tick` to ok. 1,2% próbek w nocy i 1,6% wieczorem. Koszt postoju przy aktywnych sesjach opisuje punkt 4.
3. **Indeksu do czytania.** Pytania przez wiele kart (`tools/coordinator/metrics.py`, `decisions.py`, raport) czytają
   za każdym razem wszystkie dzienniki (84,19 MiB treści w 69 dziennikach, 16.11 ~14:12). Dziennik usługi **jest już
   dopisywany z `fsync`** (`cardflow/journal.py:481`, `_append`), ale każde dopisanie poprzedza świeży odczyt całej
   historii karty (`journal.py:457`, `append` → `read_fresh`), a po nim `_restore` przepisuje całą kopię dziennika
   na Tablicy (`journal.py:597`). Commit Tablicy zbiera zestaw zmienionych ścieżek (`cardflow_monitor.py:429`, `commit`
   → `take_dirty`), więc nie jest to prosty „jeden commit na zdarzenie”. Repozytorium Tablicy: `.git` 401,38 MiB
   (`du -s -B1`, 16.11 ~14:12); 21 298 commitów od 27.10 (16.11 15:55).
4. **Skanów zależnych od zmian, nie od zegara.** Obok silnika usługa okresowo skanuje sesje i koordynację:
   - `monitor.py`: co 5 s przeliczenie stanów sesji i sonda procesów (`TICK_MS`, `monitor.py:32`), co 30 s pełny
     przegląd katalogów sesji (`RESCAN_MS`, `monitor.py:33`);
   - `coordination_monitor`: odświeżenie co 20 s (`REFRESH_MS`, `coordination_monitor.py:20`), a dodatkowo po każdej
     zmianie listy sesji, najwyżej co 4 s (`MIN_GAP_MS`, `coordination_monitor.py:21`; połączenie w `app.py:680`);
     odświeżenie przegląda dzienniki koordynatorów (`coordination/scanner.py:404`, `_claude_candidates`) i czyta
     worktree oraz gałęzie poleceniami `git` (`coordination/repoplans.py`).

   Przy aktywnych sesjach to one, a nie silnik, dominują koszt postoju: wieczorny przebieg 15.11 — średnia 32,1%,
   maksimum 47,9%, 4 z 10 okien ponad progiem, Snapshot p95 890 ms; w profilu skoki ze `scanner.refresh` (`realpath`
   w `~/.claude`), `coordination_monitor._scan` i podprocesów `git`. Powiązanie ze skanami jest diagnozą profilu, nie
   kontrolowanym eksperymentem przyczynowym. Nocny PASS (19,8% / 17,4%) był mierzony bez aktywnych sesji, a okno M5
   będzie je miało. Obserwatory plików i doczytywanie przyrostowe już istnieją (`docs/tide-daemon.md`, „Odświeżanie”),
   więc zadaniem jest usunięcie powtarzanych skanów między konsumentami, nie dodanie obserwatora.

Rozważane były też: broker komunikatów (RabbitMQ), baza danych zamiast plików i osobna baza wektorowa.

## Decyzja

- **Nic z ADR-7 w v1.** Zakres v1 i plan K-109 bez zmian; wszystkie karty poniżej startują po M5.
- **Źródło prawdy bez zmian.** Karty i plany w Markdownie oraz dziennik karty (JSONL) zostają jedynym źródłem stanu.
  [ADR-4](0004-stan-i-swiezosc.md) obowiązuje: bramki przed skutkiem czytają dziennik świeżo.
- **Spis zdarzeń.** Jeden rejestr typów: nazwa, pola, wersja typu, moduł-właściciel. Pierwsze pozycje to `command.*`
  z [ADR-1](0001-wykonanie-i-jedno-wykonanie.md). Zdarzenia spoza kart (próg limitu, tryb pracy, termin) dostają typ
  w tym samym rejestrze.
- **Indeks pochodny.** SQLite z biblioteki standardowej, w katalogu stanu usługi. Da się go skasować i odbudować
  ze źródeł. Służy widokom i zestawieniom: globalny numer zdarzenia, zapytania przez wiele kart, wyszukiwanie
  pełnotekstowe. **Nie jest źródłem decyzji** — żadna bramka przed skutkiem go nie czyta. Wymagania pierwszego
  ticketu karty A (K-134):
  - **Tożsamość i kursor.** Kasowalny SQLite nie jest jedynym miejscem trwałej tożsamości zdarzenia: zdarzenie ma
    trwały identyfikator w źródle, a odbiorca czyta po kursorze `(epoka indeksu, numer)`. Odbudowa indeksu zmienia
    epokę i wymusza ponowną synchronizację odbiorców; numer nie jest dowodem zgody.
  - **Markdown też jest źródłem zmian.** Indeks obejmuje dzienniki oraz wskazane dokumenty kart, planów i notek,
    z wersją dokumentu i obsługą edycji zewnętrznej, usunięcia i podmiany pliku.
  - **Utrata sygnału jest odbieralna.** Metoda pobrania różnicy, ograniczenie paczki, rozpoznanie starego kursora
    i powrót do pełnego odczytu należą do karty A, nie do późniejszego dopracowania.
  - **Zamknięta lista zastępowanych pamięci i odbiorców.** Pierwszy zakres: Tablica i przepływ, wskazani klienci,
    raportowanie koordynatora — nie przebudowa wszystkich 11 sygnałów bez treści.
- **Doręczanie: zdarzenie budzi, stan decyduje.**
  - Sygnał D-Bus niesie kartę i kursor zdarzenia; odbiorca dociąga różnicę po kursorze. Zgubiony sygnał nie gubi danych.
  - Silnik rusza kartę, gdy coś się w niej stało: koniec jednostki systemd (z sygnału systemd na D-Bus), decyzja
    użytkownika, termin. Pełny przegląd zostaje rzadko, jako zabezpieczenie.
  - Wyzwalacze nadal liczą się ze stanu karty (`caretaker.TRIGGERS`), nie z pojedynczego zdarzenia. Budzenie zmienia
    tylko to, kiedy są sprawdzane.
  - **Budzenie nie wchodzi do v1.** Uzasadnieniem budzenia jest czas reakcji przy trwających krokach (koniec procesu →
    trwały wynik → rozpatrzenie karty), nie CPU postoju. Udział próbek czasu ściennego nie jest wiarygodnym
    ograniczeniem zysku w CPU procesu, więc ADR nie szacuje go liczbą; czas reakcji mierzymy przed startem karty B.
- **Wyzwalacze.** Wzorem są rejestry opiekuna `TRIGGERS` i `ACTIONS` (`cardflow/caretaker.py`), rozwijane w K-79:
  akcja z zamkniętej listy, zadziałanie zapisuje zdarzenie z przyczyną. Reguły ponad kartami (limit, pory dnia, tryb
  pracy) dostają wspólny rejestr tego samego kształtu jako osobna karta R (K-137) po K-52, razem z wpisem K-53
  „profile pracy po v1” (13.11) i zgodnie z [ADR-3](0003-budzet-pracy.md): K-52 odpowiada na „ile wolno przyjąć”,
  rejestr na „jakie ustawienia pracy obowiązują i dlaczego”; nie tworzy drugiej kolejki ani drugiej autoryzacji.
  Wyzwalacze użytkownika z zamkniętych szablonów — warunkowo w v1.1 (K-138); dowolne wyrażenia, skrypty i łańcuchy
  reguł — v2.
- **Magazyn dziennika.** Dziennik zostaje dopisywany z `fsync`; zmieniamy świeży odczyt całej historii przed każdym
  zapisem i przepisywanie całej kopii na Tablicy przez `_restore`, a commity Tablicy grupujemy rzadziej niż co
  zdarzenie. Rzadszy commit nie może opóźnić trwałego zapisu zamiaru ani odpowiedzi na działanie użytkownika.
- **Bramka architektury** (masterplan §3 pkt 2, decyzja właściciela 16.11): nowe karty nie dokładają kolejnej ręcznej
  pamięci podręcznej stanu ani sygnału D-Bus bez treści; potrzeba odczytu różnicowego idzie do karty A (K-134).
- **Czego nie robimy:**
  - brokera komunikatów (RabbitMQ i podobne) — D-Bus jest lokalną szyną, a trwały dziennik z kursorem zdarzenia daje
    odbiorcy nadrabianie po restarcie;
  - bazy danych jako źródła prawdy;
  - osobnego serwera wektorowego — wektory, jeśli będą potrzebne, w tym samym indeksie (`sqlite-vec` albo mnożenie
    w pamięci jak w `krunner/semantic.py`), po wyszukiwaniu pełnotekstowym.

## Konsekwencje

- Odrzucone:
  - **RabbitMQ:** jedna maszyna, jeden użytkownik, jeden proces piszący; każde zdarzenie byłoby zapisywane w dwóch
    miejscach i uzgadniane; dodatkowa usługa do instalacji wbrew kierunkowi wydania publicznego.
  - **Baza zamiast plików:** decyzje 57 i 84 w `docs/decisions.md` oraz audyt z 12.11.2026.
  - **Osobna baza wektorowa:** decyzja 75; przy dziesiątkach tysięcy krótkich rekordów wystarcza jeden plik.
  - **Budzenie w v1:** przesłanka (pomiar K-125 powyżej 30%) odpadła, a budzenie nie dotyka zmierzonego kosztu postoju.
  - **Karta B razem z K-76:** K-76 jest czystym refaktorem („publiczny D-Bus i dziennik bez zmian; żadnych nowych
    funkcji przy okazji”, `tide-board/cards/K-76.md:42–43`), a budzenie zmienia zachowanie tego samego `tick`/`_advance`
    ([ADR-6](0006-granice-modulow-silnika.md)). K-76 idzie przed B, nie razem.
- **Indeks jest kolejną kopią stanu** (diagnoza P6 audytu: stan w czterech kopiach). Dlatego ma zastąpić istniejące
  ręczne pamięci, a nie dołożyć piątą, i ma jedną regułę świeżości: kursor `(epoka indeksu, numer)`.
- **Spis zdarzeń staje się umową.** Zmiana pól typu wymaga podniesienia wersji typu i odczytu obu wersji albo migracji.
- **Podwójne zadziałanie:** budzenie i przegląd zabezpieczający mogą trafić na tę samą zmianę; chroni przed tym
  istniejąca para „zamiar → wynik”.
- **Karta B ma dwie osobno mierzone części:** najpierw zapis, kopia i commity, potem budzenie — inaczej nie wiadomo,
  skąd pochodzi poprawa albo regresja.
- **Ryzyka z właścicielem w kartach:** rozjazd kursora po odbudowie SQLite (A); publikacja zdarzenia przed trwałym
  zapisem, opóźnienie odpowiedzi przez rzadsze commity, utrata budzenia po restarcie D-Bus, ponowne wykonanie po
  budzeniu i przeglądzie (B); pomylenie profilu pracy z uprawnieniem (R/U).

**Co temu dziś przeczy:**

1. typy zdarzeń jako napisy w modułach, bez rejestru pól;
2. sygnały bez treści i pobieranie całego stanu przez okna i widżety;
3. przegląd wszystkich kart co 5 s i `systemctl is-active` dla każdego trwającego kroku — koszt czasu reakcji, nie
   zmierzonego postoju;
4. skanowanie wszystkich dzienników przez narzędzia koordynatora i raport;
5. świeży odczyt całej historii karty przed każdym dopisaniem i przepisanie całej kopii na Tablicy przez `_restore`;
6. okresowe skany sesji (`monitor.py`, 5 s i 30 s) i koordynacji (`coordination_monitor`, 20 s oraz najwyżej co 4 s
   po zmianie sesji) z podprocesami `git` — główny koszt postoju przy aktywnych sesjach.

## Karta wykonawcza i kamień

Wszystko po M5, tag `v1.1`; kolejność według masterplanu §2 (kolejność = priorytety). Pozycje ADR-7 w K-53 przestały
być osobnym backlogiem — każda ma kartę docelową.

- **Tor poza silnikiem:**
  - **K-133 (S)** — skany sesji i koordynacji tylko po zmianie; **warunkowa**: tylko po powtórnym pomiarze postoju
    z aktywnymi sesjami, który potwierdzi koszt. Pierwsza w torze, niezależna technicznie od A i B.
  - **K-134 (A)** — spis zdarzeń, odbudowywalny indeks, odczyt różnicowy i FTS; cztery wymagania z „Decyzji” w tickecie
    1. Po zatwierdzeniu ADR-7; obok K-82/K-83 tylko po rozdzieleniu plików (`dbus_service.py`, monitory — wpięcia
    kolejno).
  - **K-63 zawężona** — zbieranie zużycia, przypisanie do kroków, prosty widok, liczone z indeksu; po K-134. Estymacje,
    Monte Carlo, data końca i samokalibracja — v2.
  - **K-140 (Q)** — wyszukiwanie po znaczeniu w tym samym indeksie; **warunkowa**: tylko gdy FTS z K-134 nie wystarcza
    na zbiorze 30 pytań.
- **Tor silnika:**
  - **K-135 (B)** — szybki trwały zapis (część 1), potem budzenie właściwych kart (część 2). Po odbiorze K-76 (nie razem
    z nią), po kontrakcie zdarzeń K-134 i po decyzji, czy dziennik zostaje w repozytorium Tablicy.
  - **K-136 (I)** — „Napisz do karty”, jedno wejście; obok ADR-7, bez zależności od K-134 i K-137; po K-79 etap 2,
    K-108 i K-107 A/B.
  - **K-137 (R)** — rejestr reguł ponad kartami i profile pracy; po K-52, K-79 i K-134, zgodnie z ADR-3.
  - **K-138 (U)** — wyzwalacze użytkownika z zamkniętych szablonów; **warunkowa**: po K-137 i 3 scenariuszach
    właściciela.
- **v2:** wyzwalacze jako dowolne wyrażenia i skrypty; K-63 poza zawężeniem.

## Rozstrzygnięcia właściciela (16.11.2026)

1. **Budzenie silnika w v1 — nie.** Warunek „pomiar K-125 nie zejdzie do 30%” nie zaszedł (PASS 19,8% / 17,4%),
   a budzenie nie odpowiada na zmierzony koszt postoju. Ryzyko dla v1 jest inne: postój przy aktywnych sesjach
   (punkt 4 „Kontekstu”) — odpowiada na nie warunkowa K-133.
2. **Bramka architektury — tak**, jako jedno zdanie w masterplanie §3 pkt 2 (treść w „Decyzji”).
3. **Kilka kart:** K-133, K-134, K-135, K-136, K-137, K-138, K-140 i zawężona K-63, jak w „Karcie wykonawczej”.

## Pytania otwarte

- Czy spis jest egzekwowany testem, czy odmową przy dopisaniu zdarzenia spoza rejestru (warunek K-134).
- Czy dziennik zostaje w repozytorium Tablicy (warunek K-135). Bez ekstrapolacji: przez 7 dni zapisywać zajęte bajty
  całego `.git` w MiB (1 048 576 B), osobno obiekty luźne i paczki, przyrost commitów i zdarzeń oraz fakt wykonania GC;
  przyrost MiB na dobę liczyć przy porównywalnym stanie pakowania. Punkt wyjścia: `.git` 401,38 MiB (`du -s -B1`,
  16.11 ~14:12), w tym luźne 95,61 MiB i paczki 294,16 MiB (`git count-objects -v`).
