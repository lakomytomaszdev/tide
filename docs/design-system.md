# Tide Mist — system projektowy

Stan na 26.10.2026. Opis wyglądu wariantu **Mist** motywu Tide dla KDE Plasma 6: zasady, tokeny, komponenty i brief na tapetę. Wszystkie liczby pochodzą z `theme/palette/mist.json`, które jest jedynym źródłem prawdy — jeśli ten dokument i paleta się różnią, wygrywa paleta.

## Charakter

Mist to miękki, ciemny grafit z tealem jako podpisem i pastelami do statusów, w układzie inspirowanym macOS. To motyw do codziennej pracy z agentami AI, więc spokój tła i czytelność stanów są ważniejsze niż efekt.

- **Jedno źródło prawdy.** Kolory, promienie, przezroczystości i czcionki są w palecie. Generatory ją czytają, wartości nie są wpisywane na sztywno.
- **Grafit, nie stal.** Tła są neutralne, bez niebieskiego odcienia (w przeciwieństwie do Nord/Nordic).
- **Jeden akcent.** Teal to akcent i stan „pracuje”. Pastele mają stałe znaczenia, nie są dekoracją.
- **Miękkość.** Duże zaokrąglenia, cienkie ramki o niskim kontraście, przezroczystość z rozmyciem zamiast ciężkich cieni.
- **Czytelność ponad styl.** Każdy tekst ma kontrast co najmniej 4,5:1 względem tła (sprawdzają to testy).
- **Bez emoji i bez gradientów jako ozdoby.** Jedyne miękkie przejścia kolorów są na tapecie (fale za kotem albo plamy).

## Paleta

### Tła i tekst

| Token | Kolor | Gdzie |
| --- | --- | --- |
| `background.desktop` | `#181b1e` | Pulpit, baza tapety, tekst na zaznaczeniu |
| `background.view` | `#1a1d20` | Listy, pola tekstowe, wnętrza widoków |
| `background.window` | `#1d2124` | Tło okien, nieaktywny pasek tytułu |
| `background.titlebar` | `#23272b` | Aktywny pasek tytułu, nagłówki |
| `background.panel` | `#24282c` | Paski Plasmy i dock (z przezroczystością) |
| `background.card` | `#262a2e` | Karty, przyciski, podpowiedzi, pola na ekranach startowych |
| `text.primary` | `#eceff1` | Tekst główny |
| `text.secondary` | `#c9ced2` | Tekst pomocniczy, etykiety |
| `text.muted` | `#a9b0b5` | Opisy, czasy, elementy nieaktywne |

### Akcent i pastele

| Token | Kolor | Znaczenie |
| --- | --- | --- |
| `accent` = `pastel.teal` | `#6fd6c6` | Akcent: zaznaczenie, fokus, kropka uruchomionej aplikacji. Stan „agent pracuje” |
| `pastel.lavender` | `#b9a7f5` | Drugi kolor marki: logo, worktrees, RAM |
| `pastel.peach` | `#f5b99a` | „Czeka na ciebie”, dysk. Limit AI skończy się przed resetem (`semantic.runout`) |
| `pastel.butter` | `#ecd58c` | Uwaga: zmiany, niescalone commity, 16–40% limitu. Przycisk minimalizacji |
| `pastel.rose` | `#f2a0b3` | Błąd, mało limitu (≤15%), usuwanie. Przycisk zamknięcia |
| `pastel.mint` | `#9fe3b8` | Dobrze: scalony, dużo limitu (>40%). Przycisk maksymalizacji |
| `pastel.sky` | `#a6c8f0` | Linki w terminalu, Codex. Stan „agent skończył” (`semantic.done`, puste kółko) — informacja, nie wezwanie |

Na tealowym zaznaczeniu tekst jest ciemny (`background.desktop`) — jasny tekst na tealu nie przechodzi testu kontrastu.

## Typografia

| Rola | Czcionka | Rozmiar | Waga |
| --- | --- | --- | --- |
| Interfejs, menu, paski | Geist | 10 pt (ok. 13 px) | 400 |
| Najmniejszy czytelny tekst | Geist | 8 pt | 400 |
| Tytuł okna | Geist | 10 pt | 500 |
| Nagłówki w popupach | Geist | 15 px | 600 |
| Liczby w widżetach | Geist Mono | 11–18 px | 600 |
| Zegar na ekranie blokady i logowania | Geist | ok. 96 px | 600 |

Geist v1.7.2 (licencja OFL), instalowany do katalogu użytkownika. Rider i terminal zostają przy swoich czcionkach. Tekst interfejsu jest po polsku.

## Kształty i głębia

| Token | Wartość | Gdzie |
| --- | --- | --- |
| `radius.small` | 9 px | Podpowiedzi, małe przyciski |
| `radius.frame` | 10 px | Górne rogi okien (dolne proste — ograniczenie Aurorae) |
| `radius.bar` | 14 px | Górny pasek jako pigułka |
| `radius.card` | 18 px | Popupy, karty, pole hasła |
| `radius.window` | 22 px | Duże powierzchnie na mockach |
| `radius.dock` | 28 px | Dock i panele boczne |
| `size.bar` / `size.barSlim` | 34 / 30 px | Pasek główny / pasek lewego monitora |
| `size.sidebar` | 420 px | Szerokość paska bocznego na lewym monitorze |
| `opacity.panel` | 0,7 | Paski przy włączonym rozmyciu |
| `opacity.popup` | 0,85 | Popupy |
| `opacity.withoutBlur` | 0,95 | Gdy KWin nie rozmywa tła |
| `opacity.border` | 0,07 | Ramka 1 px w kolorze tekstu |
| `shadow.size` / `shadow.opacity` | 12 px / 0,3 | Cień paneli i popupów |

Przy zmaksymalizowanym oknie Plasma przykleja pływające panele do krawędzi i robi je nieprzezroczystymi — to zachowanie systemu.

## Komponenty

- **Górny pasek (główny monitor):** pigułka 34 px — logo jako przycisk menu, globalne menu, zegar z datą w jednej linii pośrodku („pon. 26 paź 17:05”), skróty użytkownika, zasobnik.
- **Pasek lewego monitora:** 30 px — globalne menu i zegar.
- **Pasek boczny (lewy monitor):** 420 px, pływający, przy lewej, zewnętrznej krawędzi, od dołu smukłego paska do dołu ekranu, widoczny, dopóki nie zasłania go okno (potem krawędź albo Meta+S). Ramka panelu z prefiksem `west` i promieniem `dock` (margines treści 10 px). Od góry: nagłówek „Tide” z plakietką skrótu (Geist Mono 11 px na tle `card`, tekst w linii z treścią kafelków), potem spokojna kolumna osobnych **kafelków paska** (`SidebarTile`): tło `card`, ramka 1 px w kolorze tekstu o kryciu `opacity.border`, promień `card`, 14 px wnętrza, mały nagłówek (9 pt, 600, `text.secondary`) z miejscem na krótką informację albo przycisk-link po prawej, ok. 14 px przerwy między kafelkami (10 px w kafelku i 4 px panelu). Wolne miejsce zostaje na dole (rozciągany odstęp na końcu panelu); całość mieści się w ok. 1040 px bez przewijania (`make preview-widgets` → `sidebar-*.png`).
  - **Agenci:** jedna linia na sesję, 30 px — kropka stanu (teal pracuje, brzoskwinia czeka, puste błękitne kółko skończył), projekt, krótkie słowo stanu w kolorze stanu („czeka”, „pracuje”, „skończył”, „bezczynna”), czas („3 min”, „1 h”) w Geist Mono. Czekający wiersz ma lekkie tło w brzoskwini i mały pełny przycisk „Przejdź” (sesja Codex Desktop: „Otwórz w Codex”); inne wiersze przycisku nie mają (kolumna jest zarezerwowana, żeby słowa i czasy stały w równych kolumnach). Skończone (krycie 0,72) i bezczynne (0,55) przygaszone. Czekający u góry, najwyżej 6 wierszy, reszta pod linkiem „+N więcej”, który otwiera pełną listę w okienku obok paska; zakończone ukryte za „Pokaż zakończone (N)”. W nagłówku liczniki słowami jak w docku. Te same wiersze (`SessionRows`, dane z `AgentRows`) rysuje popup kafelka agentów w docku i okienko „+N więcej” — tam bez limitu (przewijane) i z drugą, wyciszoną linią (8 pt): na co czeka i przy czym albo narzędzie, „+N podagentów”, gałąź, tytuł. Koordynator (sesja, która zleca pracę podagentom) ma przy projekcie postęp ticketów w sky („TOM-20 · 8/13”, link do okna Tide Sesje z tym koordynatorem); link „Sesje” stoi w nagłówku popupu, a w pasku — w dolnym wierszu, gdy jakiś koordynator działa.
  - **System:** wiersze-mierniki „podpis | pasek 4 px | wartość” (CPU teal, RAM lawenda, Dysk brzoskwinia — najpełniejsza partycja) ze wspólną szerokością podpisów i wartości, pod CPU historia z ostatniej minuty (28 px), karta graficzna jednym zdaniem („GPU 31% · VRAM 0,6/8 GB”), partycje po jednej linii z paskiem („/ 9,5 GB wolne”; przy jednej partycji tylko dopisek pod paskiem Dysk), przy zajętości powyżej 85% jedna wyciszona linia „Dysk prawie pełny” (z „worktrees zwolnią X” tylko wtedy, gdy jest co sprzątać) i link „Analiza dysku”. „Monitor systemu” jako link w nagłówku.
  - **Worktrees:** „● 2 w pracy · ● 0 do sprzątnięcia (16 GB)” — tym samym językiem co dock, ale zawsze z obiema liczbami (zero wyciszone), link „Otwórz okno” w nagłówku.
  - **Limity AI:** dwa wiersze-mierniki (GPT, Claude) z niższym z pozostałych limitów w kolorze poziomu i wyciszonym „tydzień · reset poniedziałek 20:00” pod paskiem; problem z usługą to jedna wyciszona linia.
  - **Przyciski** to `TextButton` (Qt Quick Templates `AbstractButton`: rola Button, Tab, spacja i Enter): link w kolorze akcentu albo pełny w pastelu stanu („Przejdź”). Mock: artboard „Sidebar” na kanwie.
- **Dock:** pełna szerokość głównego monitora, 64 px. Aplikacje pośrodku, kafelki widżetów z prawej: agenci, worktrees, system, Limity AI.
- **Kafelek widżetu:** osobna karta 48 px wysokości (dock 64 px) z promieniem `tile` (12 px), tło w pastelu widżetu o kryciu ~0,11 (po najechaniu ~0,2) z ramką w tym pastelu (~0,24), 12 px odstępu między kafelkami. Zawsze dwie linie: etykieta w `text.secondary` (8 pt: „Agenci”, „Worktrees · 5 w pracy”, „System”, „Limity AI”) i wartość słowami (10 pt), liczby w Geist Mono. Kolor mówi o stanie. Wspólne komponenty `DockTile` i `TileMeter`, teksty i liczba mnoga w `Texts`, podgląd: `make preview-widgets`.
  - **Agenci** (teal, brzoskwinia gdy ktoś naprawdę czeka): „● 2 pracują · ● 1 czeka · ○ 2 gotowe”, tylko niezerowe części; skończone sesje mają puste błękitne kółko (`semantic.done`) i nie zmieniają koloru kafelka; bez sesji wyciszone „brak sesji”.
  - **Worktrees** (lawenda): „15 do sprzątnięcia · 16 GB”, ile w pracy przy etykiecie; bez niczego do sprzątania „nic do sprzątnięcia”.
  - **System** (sky): „CPU 34%  RAM 61%  Dysk 88%” z cienkim paskiem pod każdym miernikiem (teal, lawenda, brzoskwinia). Pierścienie w 48 px miały ok. 26 px i nieczytelną liczbę w środku, więc kafelek pokazuje procent tekstem i pasek.
  - **Limity AI** (kolor niższego limitu): „GPT 91%  Claude 86%” z paskami w kolorze poziomu.
  - **Zadanie** (teal, zaraz po agentach): „＋ Zadanie” i „nowe dla agenta” — wejście do okna nowego zadania.
  - Każdy kafelek ma podpowiedź z jednym zdaniem wyjaśnienia, np. „Sesje agentów AI: 2 pracują, 1 czeka na ciebie”. Cztery kafelki z odstępami mają do ~850 px (agenci z trzema częściami do 240 px).
- **Okno „Nowe zadanie”** (mock #22, `apps/newtask`): tło `background.window`, nagłówek „Co ma zrobić agent?” (21 px, 600) z plakietką skrótu w Geist Mono, pola na `background.view` z ramką 1 px tekstu o kryciu ~0,09 (fokus: teal ~0,75), promień `frame` + 2. Repozytorium jako lista rozwijana (romb w tealu, nazwa, „zmiany 12 min temu”), obok przełącznik „Claude Code / Codex” (wybrany: teal ~0,16 z obwódką). Pod zadaniem wyciszona podpowiedź klawiszy i licznik znaków. Zrzuty jako miniatury 150×92 z nazwą pliku w Geist Mono i ×, „Zrób zrzut” w przerywanej ramce. Karta „Nowy worktree” na `background.card` z przełącznikiem w tealu i gałęzią w lawendowym znaczniku (jak gałęzie w Worktrees) z „✎ zmień”; ostrzeżenia (zmiany na master) w maśle, błędy w różu w ramce. Stopka: co się stanie, „Anuluj” z obwódką i pełny teal „Uruchom agenta Ctrl+↵”. Znacznik „nowe” przy świeżej sesji: teal ~0,16, tekst w tealu, 8 pt.
- **Okno „Tide Sesje”** (mock zatwierdzony 26.10.2026, `apps/sessions`): tło `background.window`, trzy kolumny — koordynatorzy (290 px: nagłówek „KOORDYNATORZY” 11,5 px wersalikami w `text.muted`, pozycja z kropką stanu, projektem 14 px/600, etykietą ticketu w sky Geist Mono i licznikami ● w toku teal, ● czeka brzoskwinia, ○ gotowe; zaznaczona — teal ~0,08 z obwódką ~0,30; zakończone przygaszone; pod spodem „POJEDYNCZE SESJE”), tablica (tytuł 21 px/600, gałąź w lawendowym znaczniku Geist Mono, „koordynator · Claude Opus”, pasek postępu 6 px złożony z mięty, lawendy, tealu i brzoskwini; pięć kolumn na `background.panel` ~0,55 z promieniem 16 px: Do zrobienia, W toku teal, Czeka brzoskwinia, Review lawenda, Gotowe puste błękitne kółko; karty `background.card`, promień 12 px, ID ticketu w sky Geist Mono, do trzech rund podagentów z kropką i czasem, czekająca w brzoskwini z pytaniem, gotowe przygaszone z „✓ review OK” w mięcie, zaznaczona — tealowa obwódka) i szczegóły (380 px: ID, tytuł 17 px/600, znaczniki gałęzi i stanu, oś czasu z kropkami na szynie, cytat MAJOR na brzoskwiniowym tle z paskiem, przyciski-pigułki na dole). Główna akcja „Przejdź do koordynatora” w pełnym tealu. „Pokaż wynik” zastępuje tablicę raportem podagenta (Markdown) i ostatnimi krokami jego pracy.
- **Popup:** nagłówek z ikoną i tytułem (15 px, 600), pod nim karty; promień 18 px.
- **Karta:** tło `card` albo przyciemnione tło pastelu stanu (krycie ~0,08) z ramką w tym pastelu (~0,25–0,35). Karta wymagająca uwagi jest w brzoskwini. (Sesje agentów nie mają już kart — popup używa zwartych wierszy z paska bocznego, decyzja 44.)
- **Miernik:** pasek 4–6 px na tle tekstu o kryciu 0,1; wypełnienie: mięta >40%, masło 16–40%, róż ≤15%. W kafelkach docka pasek ma 3 px; System i Limity AI malują go w kolorze miernika albo poziomu.
- **Przyciski okna:** po prawej, minimalizuj · maksymalizuj · zamknij — pastelowe kółka 13 px (masło, mięta, róż) co 8 px; w oknie nieaktywnym szare, ikonki dopiero po najechaniu.
- **Ekran ładowania:** przyciemniona tapeta (`splash.dim` 0,35 koloru pulpitu; przy kocie — kot z otwartymi oczami w kadrze monitora), wejście przenikaniem `splash.fadeMs` (400 ms), pośrodku pełne logo 128 px, 44 px niżej pasek 220×4 px — tor w kolorze tekstu o kryciu `opacity.outline`, wypełnienie `accent`, promień `radius.small` ograniczony do połowy wysokości. Logo „oddycha” (krycie 1 → 0,78, cykl `splash.pulseMs`), bez tekstu.
- **Ekran startowy (Plymouth):** płaskie tło `background.desktop`, pośrodku pełne logo i pasek jak na ekranie ładowania (128 px, 44 px niżej pasek 220×4 px), bez tapety — w initramfs liczy się każdy kilobajt. Wejście przenikaniem `splash.fadeMs`: kafelek, potem fale co `plymouth.waveDelayMs` (300 ms); potem fale po kolei oddychają (krycie 1 → `plymouth.breatheTo` 0,6 → 1, cykl `splash.pulseMs`), kafelek stoi. Przy wyłączaniu samo logo. Hasło dysku: podpowiedź z systemu w `text.secondary`, pole jak na ekranie blokady (`lockscreen.fieldWidth` × `fieldHeight`, promień `card`, ramka i obwódka fokusu w akcencie), kropki w `text.primary`, „Hasło” w `text.muted`, „Caps Lock jest włączony” w `semantic.neutral` (masło); komunikaty systemu niżej w `text.muted`. Skala według najmniejszego ekranu: 0,75 / 1 / 4/3 / 2 względem 1920×1080.
- **Ekran blokady:** zegar 96 px i data u góry, pole hasła pośrodku; tło przyciemnione raz (`lockscreen.dim` 0,2). Przy kocie: czuwa tylko przy aktywnym używaniu ekranu — budzi się przenikaniem `lockscreen.wakeMs` (650 ms), po `lockscreen.dozeAfterMs` (3 s) bez działania zasypia wolniej i łagodniej (`lockscreen.sleepMs`, 1100 ms), także przy widocznym formularzu; nie zasypia w trakcie sprawdzania hasła i przy komunikacie o błędzie; bez animacji natychmiast.
- **Pulpit z kotem:** ten sam kadr monitora i to samo przenikanie co na blokadzie (`lockscreen.wakeMs` / `lockscreen.sleepMs`), ale kot zamyka oczy dopiero po `wallpaper.dozeAfterS` (15 s) bez klawiatury i myszy, a otwiera je przy pierwszym ruchu. Próg dużo dłuższy niż na blokadzie, bo pulpit jest zwykle zasłonięty oknami i widać go w krótkich przerwach — przy 3 s kot mrugałby przy każdym zawahaniu. Bez tide-daemon kot czuwa; bez animacji zmiana natychmiastowa.
- **Ekran logowania (SDDM):** zegar i data u góry pośrodku, awatar w kole, imię, pole hasła z tealowym przyciskiem, „Zmień użytkownika”; wybór konta jako karty (awatar, imię, nazwa konta, zaznaczenie tealową obwódką) i karta „Inny użytkownik”. Tło przyciemnione raz (`opacity.loginDim` 0,2) i nieruchome przy zmianie widoku.
- **Terminal (Konsola, #26):** karty jak pigułki kafelków (Geist, tło `card`, promień `tile`, ramka w kolorze tekstu o kryciu `opacity.outline`, aktywna z obwódką teal na tle teal ~0,11); karta agenta „● projekt · gałąź” z paskiem stanu pod tytułem (teal pracuje, brzoskwinia „czeka”, „○” skończył bez koloru). Pole terminala z kryciem `opacity.panel` i rozmyciem, Geist Mono `terminal.fontPt`, margines `terminal.marginPx`, bez paska przewijania i menu, w prawym dolnym rogu trzy fale z kryciem `terminal.watermark.opacity`. Prompt: chipy z tłem pastelu przesuniętym o 84% w stronę tła okna i tekstem w pastelu (projekt na `card`, gałąź lawenda, worktree sky, zmiany masło, agent w kolorze stanu), godzina po prawej, „›” teal (róż po błędzie). Pasek statusu Claude Code tym samym językiem, limity w kolorach poziomu jak w widżecie Limity AI.
- **Logo „trzy fale”:** fale teal, lawenda, brzoskwinia. Wersja pełna na kafelku `card` (promień 20/64), wersja bez kafelka z grubszymi falami od 18 px, wersja symboliczna jednokolorowa.

## Ikony

Ikony aplikacji są okrągłe (baza: McMojave-circle-dark). Nakładka „Tide” dziedziczy po bazie i dokłada tylko różnice: aliasy (Toolbox JetBrains, błędne `code`), kafelki z oryginalnych ikon (okrągłe zostają, pełne grafiki wycięte w koło, przezroczyste kształty na tle w swoim kolorze), foldery w tealu, glify w pastelach dla aplikacji bez ikony. `tide-icons audit` pokazuje pokrycie, `tide-icons sync` uzupełnia nakładkę.

Zasady dla nowych ikon: okrąg, bez tekstu w środku, kolory z palety albo własne kolory marki aplikacji.

**Aplikacje z docka (#8)** mają stonowane glify zamiast kolorowych ikon: zaokrąglony kwadrat (promień 26% boku) w kolorze `pastel × 0,16 + 24` na kanał i znak aplikacji w samym pastelu, zajmujący 46% boku, wyśrodkowany. Pełne znaki-kwadraty (JetBrains) są mniejsze (40%), żeby nie przeważały nad znakami z kreski. Kolory: Konsola teal, Dolphin i WebStorm sky, Firefox peach, Rider i Discord lavender, PyCharm i ChatGPT mint, Spectacle rose, T3 Code butter, Ustawienia i Zed `text.secondary`, Steam, Docker i VS Code sky.

**Pozostałe programy (decyzja 40)** mają glify w tym samym stylu. Kolor to pastel najbliższy kolorowi marki (Telegram, Writer, Chromium sky; Brave, VLC, Blender, Toolbox peach; Android Studio, Calc, NVIDIA mint; FileZilla, Redis, dotMemory rose; DBeaver, GIMP, Gnote, Ark butter; mpv, Kvantum, KeepSecret lavender; Tide Worktrees, Tide Sesje, Kate, KDE Connect teal), a narzędzia systemowe bez własnego koloru (Edytor menu, Ośrodek informacji i pomocy, LibreOffice Math, OBS) dostają `text.secondary` jak Ustawienia. Ręczne znaki (`theme/icons/glyphs/`) rysujemy kreską 2 px w siatce 24×24 z zaokrąglonymi końcami, bez wypełnień i tekstu, jak znaki symboliczne Breeze. Lista i źródła znaków: `theme/icons/glyphs.json`, podgląd wszystkich glifów obok obecnych ikon: `make icons-preview`.

## Brief: tapeta

Zadanie dla projektanta lub agenta UI: zaprojektować tapetę dla wariantu Mist (albo jej alternatywy do wyboru).

### Monitory

| Złącze | Rozdzielczość | Położenie | Rola |
| --- | --- | --- | --- |
| `DP-2` | 2560×1440 | x=1920, y=0 (prawy) | Główny: górny pasek 34 px, dock 64 px na dole |
| `HDMI-A-1` | 1920×1080 | x=0, y=0 (lewy, wyrównany do góry) | Drugi: smukły pasek 30 px u góry |

Wspólne płótno ma 4480×1440. Lewy ekran zajmuje wiersze 0–1079, więc jego dolne 360 px płótna nigdzie się nie wyświetla. Skala 1, bez obrotu.

### Wymagania

1. **Ciągłość na styku ekranów** (kolumna x=1920, wiersze 0–1079) — obraz ma wyglądać jak jeden, rozciągnięty na oba monitory.
2. **Ciemno i spokojnie.** Baza `#181b1e`, średnia jasność niska; na tapecie leżą półprzezroczyste paski i popupy, a tekst na nich musi zostać czytelny.
3. **Strefy ciszy:** górne ~50 px obu ekranów (paski) i dolne ~90 px DP-2 (dock) — bez jasnych ani kontrastowych elementów.
4. **Tylko kolory palety** (plus ich przyciemnienia/rozjaśnienia): tła, teal, lawenda, brzoskwinia; ewentualnie mięta i niebieski `sky` jako drobny akcent.
5. **Bez tekstu, logotypów i rozpoznawalnych motywów cudzych marek.**
6. **Bez pasm na gradientach** — ziarno lub dithering, PNG 8 bit na kanał.
7. Ta sama grafika (wersja DP-2, przyciemniona) trafia też na ekran blokady i logowania, więc środek kadru zostanie przykryty zegarem i polem hasła — tam też bez mocnych detali.

### Obecna wersja

Mist używa wariantu **`cat`**: ilustracji kota (kremowo-beżowe futro, czekoladowa maska, uszy i łapy, niebieskie oczy) leżącego na falach w grafitowo-tealowo-lawendowych barwach, z wersją z zamkniętymi oczami do ekranu blokady i pulpitu w bezczynności (wtyczka tapety `org.tide.cat`). Umaszczenie kota to świadomy wyjątek od zasady „tylko kolory palety”. Pliki, pochodzenie i sumy: `theme/wallpaper/assets/cat/README.md`. Kot leży na DP-2 (ok. 72% szerokości ekranu), środek DP-2 zostaje spokojny pod zegar i pole hasła.

Alternatywa **`blobs`** (`wallpaper.variant` w palecie): generator `theme/wallpaper/generate.py` rysuje płótno z 4 miękkich plam (lawenda na styku ekranów u góry, teal w lewym górnym rogu lewego ekranu i w prawym dolnym rogu DP-2, słaba brzoskwinia pośrodku DP-2) na tle `#181b1e`, z ziarnem 1,1 poziomu i stałym ziarnem losowania. Parametry plam (kolor, położenie jako ułamek płótna, rozmiar, rozciągnięcie, kąt, krycie) są w sekcji `wallpaper` palety. Podgląd bez zmiany pulpitu: `make wallpaper-preview` (wynik w `build/wallpaper`).

### Jak dostarczyć

- **Wariant parametryczny (preferowany):** zmiana sekcji `wallpaper` w `theme/palette/mist.json` albo rozszerzenie generatora o nowe kształty — wtedy tapeta dopasuje się sama, jeśli zmieni się układ monitorów.
- **Wariant gotowych obrazów:** dwa PNG nazwane po złączach (`DP-2.png` 2560×1440, `HDMI-A-1.png` 1920×1080), wycięte z jednego płótna 4480×1440 zgodnie z tabelą wyżej.
- Instalacja na pulpicie: `make install-wallpaper FORCE=1` (z kopią zapasową), cofnięcie: `make uninstall-wallpaper` albo `make restore`.

## Gdzie co jest

| Co | Gdzie |
| --- | --- |
| Paleta (źródło prawdy) | `theme/palette/mist.json` |
| Schemat kolorów KDE | `theme/colors/generate.py` |
| Styl Plasmy (ramki paneli, popupów) | `theme/plasma/` |
| Dekoracja okien (Aurorae) | `theme/decoration/` |
| Logo | `theme/logo/generate.py` |
| Ekran startowy (Plymouth) | `theme/plymouth/` (składanie, logika skryptu, atrapa), `scripts/plymouth_system.py` (instalacja) |
| Tapeta | `theme/wallpaper/generate.py`, `theme/wallpaper/variant.py` |
| Wtyczka tapety z drzemiącym kotem | `theme/wallpaper/plugin.py`, `theme/wallpaper/org.tide.cat/`, wspólny `theme/wallpaper/qml/CatBackdrop.qml` |
| Ikony | `theme/icons/`, `tools/make_tile.py` |
| Glify aplikacji z docka | `theme/icons/glyphs.json`, `theme/icons/glyphs.py`, `theme/icons/glyphs/` |
| Terminal: Konsola, prompt, pasek statusu Claude Code, motywy narzędzi | `theme/konsole/generate.py`, `theme/terminal/` |
| Widżety i wspólna biblioteka QML | `widgets/`, `widgets/lib/` |
| Decyzje i ich uzasadnienia | `docs/decisions.md` |
| Mocki (kanwa) | https://claude.ai/artifact/1h17Au71mmdEX5sNBSaAgC |
