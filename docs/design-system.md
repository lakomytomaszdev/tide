# Tide Mist — system projektowy

Stan na 26.10.2026. Opis wyglądu wariantu **Mist** motywu Tide dla KDE Plasma 6: zasady, tokeny, komponenty i brief na tapetę. Wszystkie liczby pochodzą z `theme/palette/mist.json`, które jest jedynym źródłem prawdy — jeśli ten dokument i paleta się różnią, wygrywa paleta.

## Charakter

Mist to miękki, ciemny grafit z tealem jako podpisem i pastelami do statusów, w układzie inspirowanym macOS. To motyw do codziennej pracy z agentami AI, więc spokój tła i czytelność stanów są ważniejsze niż efekt.

- **Jedno źródło prawdy.** Kolory, promienie, przezroczystości i czcionki są w palecie. Generatory ją czytają, wartości nie są wpisywane na sztywno.
- **Grafit, nie stal.** Tła są neutralne, bez niebieskiego odcienia (w przeciwieństwie do Nord/Nordic).
- **Jeden akcent.** Teal to akcent i stan „pracuje”. Pastele mają stałe znaczenia, nie są dekoracją.
- **Miękkość.** Duże zaokrąglenia, cienkie ramki o niskim kontraście, przezroczystość z rozmyciem zamiast ciężkich cieni.
- **Czytelność ponad styl.** Każdy tekst ma kontrast co najmniej 4,5:1 względem tła (sprawdzają to testy).
- **Bez emoji i bez gradientów jako ozdoby.** Jedyny „gradient” w systemie to miękkie plamy na tapecie.

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
| `pastel.peach` | `#f5b99a` | „Czeka na ciebie”, dysk |
| `pastel.butter` | `#ecd58c` | Uwaga: zmiany, niescalone commity, 16–40% limitu. Przycisk minimalizacji |
| `pastel.rose` | `#f2a0b3` | Błąd, mało limitu (≤15%), usuwanie. Przycisk zamknięcia |
| `pastel.mint` | `#9fe3b8` | Dobrze: scalony, dużo limitu (>40%). Przycisk maksymalizacji |
| `pastel.sky` | `#a6c8f0` | Linki w terminalu, Codex |

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
| `opacity.panel` | 0,7 | Paski przy włączonym rozmyciu |
| `opacity.popup` | 0,85 | Popupy |
| `opacity.withoutBlur` | 0,95 | Gdy KWin nie rozmywa tła |
| `opacity.border` | 0,07 | Ramka 1 px w kolorze tekstu |
| `shadow.size` / `shadow.opacity` | 12 px / 0,3 | Cień paneli i popupów |

Przy zmaksymalizowanym oknie Plasma przykleja pływające panele do krawędzi i robi je nieprzezroczystymi — to zachowanie systemu.

## Komponenty

- **Górny pasek (główny monitor):** pigułka 34 px — logo jako przycisk menu, globalne menu, zegar z datą w jednej linii pośrodku („pon. 26 paź 17:05”), skróty użytkownika, zasobnik.
- **Pasek lewego monitora:** 30 px — globalne menu i zegar.
- **Dock:** pełna szerokość głównego monitora, 64 px. Aplikacje pośrodku, kafelki widżetów z prawej: agenci, worktrees, system, Limity AI.
- **Kafelek widżetu:** dwie linie albo kropki z licznikami; etykiety w `text.secondary`, liczby w Geist Mono. Kolor mówi o stanie.
- **Popup:** nagłówek z ikoną i tytułem (15 px, 600), pod nim karty; promień 18 px.
- **Karta:** tło `card` albo przyciemnione tło pastelu stanu (krycie ~0,08) z ramką w tym pastelu (~0,25–0,35). Karta wymagająca uwagi (agent czeka) jest w brzoskwini.
- **Miernik:** pasek 4–6 px na tle tekstu o kryciu 0,1; wypełnienie: mięta >40%, masło 16–40%, róż ≤15%. Pierścienie w widżecie systemu tak samo.
- **Przyciski okna:** po prawej, minimalizuj · maksymalizuj · zamknij — pastelowe kółka 13 px (masło, mięta, róż) co 8 px; w oknie nieaktywnym szare, ikonki dopiero po najechaniu.
- **Logo „trzy fale”:** fale teal, lawenda, brzoskwinia. Wersja pełna na kafelku `card` (promień 20/64), wersja bez kafelka z grubszymi falami od 18 px, wersja symboliczna jednokolorowa.

## Ikony

Ikony aplikacji są okrągłe (baza: McMojave-circle-dark). Nakładka „Tide” dziedziczy po bazie i dokłada tylko różnice: aliasy (Toolbox JetBrains, błędne `code`), kafelki z oryginalnych ikon (okrągłe zostają, pełne grafiki wycięte w koło, przezroczyste kształty na tle w swoim kolorze), foldery w tealu, glify w pastelach dla aplikacji bez ikony. `tide-icons audit` pokazuje pokrycie, `tide-icons sync` uzupełnia nakładkę.

Zasady dla nowych ikon: okrąg, bez tekstu w środku, kolory z palety albo własne kolory marki aplikacji. Otwarte: styl ikon w docku (#8).

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

Generator `theme/wallpaper/generate.py` rysuje płótno z 4 miękkich plam (lawenda na styku ekranów u góry, teal w lewym górnym rogu lewego ekranu i w prawym dolnym rogu DP-2, słaba brzoskwinia pośrodku DP-2) na tle `#181b1e`, z ziarnem 1,1 poziomu i stałym ziarnem losowania. Parametry plam (kolor, położenie jako ułamek płótna, rozmiar, rozciągnięcie, kąt, krycie) są w sekcji `wallpaper` palety. Podgląd bez zmiany pulpitu: `make wallpaper-preview` (wynik w `build/wallpaper`).

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
| Tapeta | `theme/wallpaper/generate.py` |
| Ikony | `theme/icons/`, `tools/make_tile.py` |
| Konsole | `theme/konsole/generate.py` |
| Widżety i wspólna biblioteka QML | `widgets/`, `widgets/lib/` |
| Decyzje i ich uzasadnienia | `docs/decisions.md` |
| Mocki (kanwa) | https://claude.ai/artifact/1h17Au71mmdEX5sNBSaAgC |
